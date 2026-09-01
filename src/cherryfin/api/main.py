from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from cherryfin import __version__
from cherryfin.agents.orchestrator import (
    AgentOutputError,
    CherryFinancialAgent,
    UnsafeRequestError,
)
from cherryfin.core.models import (
    AnalysisRequest,
    AnalysisResponse,
    ClaimStatus,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)
from cherryfin.intelligence.claims import ClaimLedger
from cherryfin.intelligence.models import (
    ClaimQueryResult,
    ContradictionRecord,
    ContradictionStatus,
    EvidenceDocument,
    EvidenceIngestRequest,
    EvidenceIngestResult,
    IdentifierScheme,
    Instrument,
    KnowledgeQuery,
    LedgerWriteResult,
)
from cherryfin.intelligence.pipeline import EvidencePipeline
from cherryfin.intelligence.retrieval import (
    KnowledgeSourceCollisionError,
    UntrustedKnowledgeInputError,
    hydrate_analysis_request,
)
from cherryfin.intelligence.store import (
    EvidenceDependencyError,
    IdentifierConflictError,
    IntegrityConflictError,
    RecordNotFoundError,
    SQLiteIntelligenceStore,
)
from cherryfin.intelligence.store_audit import AuditEvent, AuditVerification
from cherryfin.intelligence.tenant_store import TenantStoreRegistry
from cherryfin.providers.llm import LLMProviderError, OpenAICompatibleProvider
from cherryfin.security.auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    Principal,
    Scope,
)
from cherryfin.settings import Settings
from cherryfin.tools.calculators import (
    CompoundGrowthResult,
    LoanResult,
    PortfolioRiskResult,
    calculate_compound_growth,
    calculate_loan,
    calculate_portfolio_risk,
)

settings = Settings()
stores = TenantStoreRegistry(
    settings.intelligence_store_path,
    default_tenant_id=settings.default_tenant_id,
    trust_client_timestamps=False,
)
auth_service = AuthService(
    environment=settings.environment,
    default_tenant_id=settings.default_tenant_id,
    platform_admin_api_key=settings.admin_api_key,
    tenant_credentials=settings.tenant_credentials,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    stores.close_all()


app = FastAPI(
    title="CherryFin API",
    version=__version__,
    description=(
        "Tenant-isolated point-in-time financial intelligence with evidence provenance, "
        "deterministic policy, and human-controlled execution."
    ),
    lifespan=lifespan,
)


def _build_agent() -> CherryFinancialAgent | None:
    if not settings.llm_model.strip():
        return None
    provider = OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return CherryFinancialAgent(provider=provider, settings=settings)


agent = _build_agent()


def _authenticate(
    x_cherryfin_tenant: Annotated[
        str | None,
        Header(alias="X-CherryFin-Tenant"),
    ] = None,
    x_cherryfin_actor: Annotated[
        str | None,
        Header(alias="X-CherryFin-Actor"),
    ] = None,
    x_cherryfin_key: Annotated[
        str | None,
        Header(alias="X-CherryFin-Key"),
    ] = None,
    x_cherryfin_admin_key: Annotated[
        str | None,
        Header(alias="X-CherryFin-Admin-Key"),
    ] = None,
) -> Principal:
    try:
        return auth_service.authenticate(
            tenant_id=x_cherryfin_tenant,
            actor_id=x_cherryfin_actor,
            api_key=x_cherryfin_key or x_cherryfin_admin_key,
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


PrincipalDependency = Annotated[Principal, Depends(_authenticate)]


def _authorize(principal: Principal, scope: Scope) -> None:
    try:
        auth_service.authorize(principal, scope)
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


def _tenant_store(principal: Principal) -> SQLiteIntelligenceStore:
    return stores.for_tenant(principal.tenant_id)


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied and len(supplied) <= 200 and all(ord(char) >= 32 for char in supplied):
        return supplied
    return str(uuid4())


def _inline_evidence_id(evidence_id: str) -> str:
    candidate = f"user:{evidence_id}"
    if len(candidate) <= 120:
        return candidate
    digest = hashlib.sha256(evidence_id.encode()).hexdigest()
    return f"user:{digest}"


def _sanitize_analysis_request(request: AnalysisRequest) -> AnalysisRequest:
    if request.claims:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Inline financial claims are not accepted by the public analysis API. "
                "Ingest verified claims into the tenant ledger and use knowledge_context."
            ),
        )

    sanitized_evidence: list[Evidence] = []
    for item in request.evidence:
        sanitized_evidence.append(
            item.model_copy(
                update={
                    "evidence_id": _inline_evidence_id(item.evidence_id),
                    "kind": EvidenceKind.USER_PROVIDED,
                    "uri": None,
                    "trust_score": min(
                        item.trust_score,
                        settings.max_inline_evidence_trust,
                    ),
                    "content_sha256": None,
                    "license_tag": None,
                }
            )
        )
    payload = request.model_dump(mode="python")
    payload.update({"claims": [], "evidence": sanitized_evidence})
    return AnalysisRequest.model_validate(payload)


class CompoundGrowthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal = Field(ge=0)
    annual_rate_pct: Decimal = Field(gt=-100)
    years: int = Field(ge=0, le=200)
    compounds_per_year: int = Field(default=12, ge=1, le=365)
    periodic_contribution: Decimal = Field(default=Decimal("0"), ge=0)
    contribution_at_beginning: bool = False


class LoanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal = Field(gt=0)
    annual_rate_pct: Decimal = Field(ge=0, le=1000)
    term_months: int = Field(gt=0, le=1200)


class PortfolioRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodic_returns: Annotated[list[float], Field(min_length=2, max_length=1_000_000)]
    periods_per_year: int = Field(default=252, gt=0, le=100_000)
    annual_risk_free_rate: float = Field(default=0.0, gt=-1)


class InstrumentWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: Instrument
    created: bool
    snapshot_sha256: str = Field(min_length=64, max_length=64)


class SnapshotResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    schema_version: int
    snapshot_sha256: str = Field(min_length=64, max_length=64)


class ContradictionResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_claim_id: str | None = Field(default=None, max_length=120)
    resolution_note: str = Field(min_length=1, max_length=2000)
    dismiss: bool = False


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "CherryFin",
        "tagline": "Financial intelligence. Evidence first. Human controlled.",
        "version": __version__,
    }


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "version": __version__,
        "llm_configured": agent is not None,
        "live_execution_enabled": settings.execution_enabled,
        "tenant_store_registry": "ready",
    }


@app.get("/v1/capabilities")
def capabilities(principal: PrincipalDependency) -> dict[str, object]:
    _authorize(principal, Scope.CAPABILITIES_READ)
    return {
        "tenant_id": principal.tenant_id,
        "role": principal.role.value,
        "modes": [
            "personal_cfo",
            "investment_research",
            "portfolio_risk",
            "business_cfo",
            "trading_research",
        ],
        "intelligence": [
            "tenant_isolated_store",
            "role_based_access_control",
            "immutable_evidence_store",
            "canonical_instrument_master",
            "financial_claims_ledger",
            "point_in_time_retrieval",
            "atomic_ingestion",
            "contradiction_detection",
            "hash_chained_audit_log",
            "thai_english_statement_normalization",
        ],
        "deterministic_calculators": [
            "compound_growth",
            "loan_amortization_summary",
            "historical_portfolio_risk",
        ],
        "execution": {
            "available": False,
            "reason": (
                "Analysis API never authorizes transactions; approval and execution are separate."
            ),
        },
    }


@app.post("/v1/analyze", response_model=AnalysisResponse)
async def analyze(
    request: AnalysisRequest,
    http_request: Request,
    principal: PrincipalDependency,
) -> AnalysisResponse:
    _authorize(principal, Scope.ANALYSIS_RUN)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set CHERRYFIN_LLM_MODEL to enable the analysis agent.",
        )

    store = _tenant_store(principal)
    sanitized = _sanitize_analysis_request(request)
    try:
        hydrated = hydrate_analysis_request(sanitized, store=store)
    except (UntrustedKnowledgeInputError, KnowledgeSourceCollisionError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    request_id = _request_id(http_request)
    try:
        response = await agent.analyze(hydrated)
    except UnsafeRequestError as exc:
        raise HTTPException(status_code=400, detail=list(exc.reasons)) from exc
    except (LLMProviderError, AgentOutputError) as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured model could not produce a valid structured answer.",
        ) from exc

    with store.audit_context(actor_id=principal.actor_id, request_id=request_id):
        store.append_audit_event(
            action="analysis.completed",
            resource_type="analysis",
            resource_id=str(response.request_id),
            payload={
                "answer_id": str(response.answer.answer_id),
                "mode": response.answer.mode.value,
                "evaluation_score": response.evaluation.score,
                "evaluation_passed": response.evaluation.passed,
                "claim_ids_used": response.answer.claim_ids_used,
                "evidence_ids_used": response.answer.evidence_ids_used,
                "knowledge_snapshot_sha256": hydrated.metadata.get("knowledge_snapshot_sha256"),
            },
        )
    return response


@app.post("/v1/instruments", response_model=InstrumentWriteResult)
def add_instrument(
    instrument: Instrument,
    http_request: Request,
    principal: PrincipalDependency,
) -> InstrumentWriteResult:
    _authorize(principal, Scope.INSTRUMENT_WRITE)
    store = _tenant_store(principal)
    try:
        with store.audit_context(
            actor_id=principal.actor_id,
            request_id=_request_id(http_request),
        ):
            stored, created = store.add_instrument(instrument)
    except (IntegrityConflictError, IdentifierConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return InstrumentWriteResult(
        instrument=stored,
        created=created,
        snapshot_sha256=store.snapshot_sha256(),
    )


@app.get("/v1/instruments/resolve", response_model=Instrument)
def resolve_instrument(
    scheme: IdentifierScheme,
    value: Annotated[str, Query(min_length=1, max_length=120)],
    principal: PrincipalDependency,
    venue: Annotated[str | None, Query(max_length=40)] = None,
) -> Instrument:
    _authorize(principal, Scope.INSTRUMENT_READ)
    try:
        return _tenant_store(principal).resolve_instrument(
            scheme=scheme,
            value=value,
            venue=venue,
        )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/evidence/ingest", response_model=EvidenceIngestResult)
def ingest_evidence(
    request: EvidenceIngestRequest,
    http_request: Request,
    principal: PrincipalDependency,
) -> EvidenceIngestResult:
    _authorize(principal, Scope.EVIDENCE_WRITE)
    content_bytes = len(request.document.content_bytes or b"")
    content_text_bytes = len((request.document.content or "").encode())
    structured_bytes = (
        len(
            json.dumps(
                request.document.structured_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        if request.document.structured_payload is not None
        else 0
    )
    if content_bytes + content_text_bytes + structured_bytes > settings.max_evidence_bytes:
        raise HTTPException(status_code=413, detail="Evidence exceeds the configured byte limit.")

    store = _tenant_store(principal)
    pipeline = EvidencePipeline(store)
    try:
        with store.audit_context(
            actor_id=principal.actor_id,
            request_id=_request_id(http_request),
        ):
            return pipeline.ingest(request)
    except (
        EvidenceDependencyError,
        IdentifierConflictError,
        IntegrityConflictError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/evidence/{evidence_id}", response_model=EvidenceDocument)
def get_evidence(
    evidence_id: str,
    http_request: Request,
    principal: PrincipalDependency,
    include_content: bool = False,
) -> EvidenceDocument:
    required_scope = Scope.EVIDENCE_CONTENT_READ if include_content else Scope.EVIDENCE_READ
    _authorize(principal, required_scope)
    store = _tenant_store(principal)
    try:
        document = store.get_evidence(evidence_id, include_content=include_content)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if include_content:
        with store.audit_context(
            actor_id=principal.actor_id,
            request_id=_request_id(http_request),
        ):
            store.append_audit_event(
                action="evidence.content_read",
                resource_type="evidence",
                resource_id=evidence_id,
            )
    return document


@app.post("/v1/claims", response_model=LedgerWriteResult)
def add_claim(
    claim: FinancialClaim,
    http_request: Request,
    principal: PrincipalDependency,
) -> LedgerWriteResult:
    _authorize(principal, Scope.CLAIM_WRITE)
    store = _tenant_store(principal)
    ledger = ClaimLedger(store)
    server_claim = claim.model_copy(
        update={
            "asserted_at": datetime.now(UTC),
            "status": ClaimStatus.ACTIVE,
        }
    )
    try:
        with store.audit_context(
            actor_id=principal.actor_id,
            request_id=_request_id(http_request),
        ):
            return ledger.record(server_claim)
    except (EvidenceDependencyError, IntegrityConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/claims/query", response_model=ClaimQueryResult)
def query_claims(
    query: KnowledgeQuery,
    principal: PrincipalDependency,
) -> ClaimQueryResult:
    _authorize(principal, Scope.CLAIM_READ)
    return EvidencePipeline(_tenant_store(principal)).query(query)


@app.get("/v1/contradictions", response_model=list[ContradictionRecord])
def contradictions(
    principal: PrincipalDependency,
    contradiction_status: Annotated[ContradictionStatus | None, Query()] = (
        ContradictionStatus.OPEN
    ),
    subject_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[ContradictionRecord]:
    _authorize(principal, Scope.CONTRADICTION_READ)
    return _tenant_store(principal).list_contradictions(
        status=contradiction_status,
        subject_id=subject_id,
        limit=limit,
    )


@app.post(
    "/v1/contradictions/{contradiction_id}/resolve",
    response_model=ContradictionRecord,
)
def resolve_contradiction(
    contradiction_id: str,
    request: ContradictionResolutionRequest,
    http_request: Request,
    principal: PrincipalDependency,
) -> ContradictionRecord:
    _authorize(principal, Scope.CONTRADICTION_RESOLVE)
    store = _tenant_store(principal)
    try:
        with store.audit_context(
            actor_id=principal.actor_id,
            request_id=_request_id(http_request),
        ):
            return store.resolve_contradiction(
                contradiction_id=contradiction_id,
                accepted_claim_id=request.accepted_claim_id,
                resolution_note=request.resolution_note,
                dismiss=request.dismiss,
            )
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/audit/events", response_model=list[AuditEvent])
def audit_events(
    principal: PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
) -> list[AuditEvent]:
    _authorize(principal, Scope.AUDIT_READ)
    return _tenant_store(principal).list_audit_events(limit=limit)


@app.get("/v1/audit/verify", response_model=AuditVerification)
def verify_audit(principal: PrincipalDependency) -> AuditVerification:
    _authorize(principal, Scope.AUDIT_READ)
    return _tenant_store(principal).verify_audit_chain()


@app.get("/v1/audit/snapshot", response_model=SnapshotResult)
def audit_snapshot(principal: PrincipalDependency) -> SnapshotResult:
    _authorize(principal, Scope.AUDIT_READ)
    store = _tenant_store(principal)
    return SnapshotResult(
        tenant_id=principal.tenant_id,
        schema_version=store.schema_version(),
        snapshot_sha256=store.snapshot_sha256(),
    )


@app.post("/v1/calculators/compound-growth", response_model=CompoundGrowthResult)
def compound_growth(
    request: CompoundGrowthRequest,
    principal: PrincipalDependency,
) -> CompoundGrowthResult:
    _authorize(principal, Scope.CALCULATOR_RUN)
    return calculate_compound_growth(**request.model_dump())


@app.post("/v1/calculators/loan", response_model=LoanResult)
def loan(
    request: LoanRequest,
    principal: PrincipalDependency,
) -> LoanResult:
    _authorize(principal, Scope.CALCULATOR_RUN)
    return calculate_loan(**request.model_dump())


@app.post("/v1/calculators/portfolio-risk", response_model=PortfolioRiskResult)
def portfolio_risk(
    request: PortfolioRiskRequest,
    principal: PrincipalDependency,
) -> PortfolioRiskResult:
    _authorize(principal, Scope.CALCULATOR_RUN)
    return calculate_portfolio_risk(**request.model_dump())


def run() -> None:
    uvicorn.run("cherryfin.api.main:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    run()
