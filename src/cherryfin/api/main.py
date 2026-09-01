from __future__ import annotations

import json
import secrets
from decimal import Decimal
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from cherryfin import __version__
from cherryfin.agents.orchestrator import (
    AgentOutputError,
    CherryFinancialAgent,
    UnsafeRequestError,
)
from cherryfin.core.models import AnalysisRequest, AnalysisResponse, FinancialClaim
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
from cherryfin.intelligence.retrieval import hydrate_analysis_request
from cherryfin.intelligence.store import (
    EvidenceDependencyError,
    IdentifierConflictError,
    IntegrityConflictError,
    RecordNotFoundError,
    SQLiteIntelligenceStore,
)
from cherryfin.providers.llm import LLMProviderError, OpenAICompatibleProvider
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
store = SQLiteIntelligenceStore(settings.intelligence_store_path)
pipeline = EvidencePipeline(store)
ledger = ClaimLedger(store)
app = FastAPI(
    title="CherryFin API",
    version=__version__,
    description=(
        "Point-in-time financial intelligence with evidence provenance, deterministic policy, "
        "and human-controlled execution."
    ),
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


def _require_admin(
    x_cherryfin_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    if settings.admin_api_key:
        if x_cherryfin_admin_key is None or not secrets.compare_digest(
            x_cherryfin_admin_key,
            settings.admin_api_key,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid CherryFin admin API key is required.",
            )
        return
    if settings.environment.casefold() not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mutating APIs are disabled until CHERRYFIN_ADMIN_API_KEY is configured.",
        )


AdminDependency = Annotated[None, Depends(_require_admin)]


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
        "intelligence_store": "ready",
    }


@app.get("/v1/capabilities")
def capabilities() -> dict[str, object]:
    return {
        "modes": [
            "personal_cfo",
            "investment_research",
            "portfolio_risk",
            "business_cfo",
            "trading_research",
        ],
        "intelligence": [
            "immutable_evidence_store",
            "canonical_instrument_master",
            "financial_claims_ledger",
            "point_in_time_retrieval",
            "contradiction_detection",
            "thai_english_statement_normalization",
            "official_filing_connector_contract",
            "licensed_market_data_connector_contract",
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
async def analyze(request: AnalysisRequest) -> AnalysisResponse:
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set CHERRYFIN_LLM_MODEL to enable the analysis agent.",
        )
    hydrated_request = hydrate_analysis_request(request, store=store)
    try:
        return await agent.analyze(hydrated_request)
    except UnsafeRequestError as exc:
        raise HTTPException(status_code=400, detail=list(exc.reasons)) from exc
    except (LLMProviderError, AgentOutputError) as exc:
        raise HTTPException(
            status_code=502,
            detail="The configured model could not produce a valid structured answer.",
        ) from exc


@app.post("/v1/instruments", response_model=InstrumentWriteResult)
def add_instrument(instrument: Instrument, _: AdminDependency) -> InstrumentWriteResult:
    try:
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
    venue: Annotated[str | None, Query(max_length=40)] = None,
) -> Instrument:
    try:
        return store.resolve_instrument(scheme=scheme, value=value, venue=venue)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/evidence/ingest", response_model=EvidenceIngestResult)
def ingest_evidence(request: EvidenceIngestRequest, _: AdminDependency) -> EvidenceIngestResult:
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
    try:
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
    include_content: bool = False,
    x_cherryfin_admin_key: Annotated[str | None, Header()] = None,
) -> EvidenceDocument:
    if include_content:
        _require_admin(x_cherryfin_admin_key)
    try:
        return store.get_evidence(evidence_id, include_content=include_content)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/claims", response_model=LedgerWriteResult)
def add_claim(claim: FinancialClaim, _: AdminDependency) -> LedgerWriteResult:
    try:
        return ledger.record(claim)
    except (EvidenceDependencyError, IntegrityConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/claims/query", response_model=ClaimQueryResult)
def query_claims(query: KnowledgeQuery) -> ClaimQueryResult:
    return pipeline.query(query)


@app.get("/v1/contradictions", response_model=list[ContradictionRecord])
def contradictions(
    contradiction_status: Annotated[ContradictionStatus | None, Query()] = (
        ContradictionStatus.OPEN
    ),
    subject_id: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[ContradictionRecord]:
    return store.list_contradictions(
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
    _: AdminDependency,
) -> ContradictionRecord:
    try:
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


@app.get("/v1/audit/snapshot", response_model=SnapshotResult)
def audit_snapshot() -> SnapshotResult:
    return SnapshotResult(snapshot_sha256=store.snapshot_sha256())


@app.post("/v1/calculators/compound-growth", response_model=CompoundGrowthResult)
def compound_growth(request: CompoundGrowthRequest) -> CompoundGrowthResult:
    return calculate_compound_growth(**request.model_dump())


@app.post("/v1/calculators/loan", response_model=LoanResult)
def loan(request: LoanRequest) -> LoanResult:
    return calculate_loan(**request.model_dump())


@app.post("/v1/calculators/portfolio-risk", response_model=PortfolioRiskResult)
def portfolio_risk(request: PortfolioRiskRequest) -> PortfolioRiskResult:
    return calculate_portfolio_risk(**request.model_dump())


def run() -> None:
    uvicorn.run("cherryfin.api.main:app", host="0.0.0.0", port=8080)


if __name__ == "__main__":
    run()
