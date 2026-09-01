from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
    KnowledgeContextRequest,
)
from cherryfin.intelligence.models import (
    AssetClass,
    EvidenceDocument,
    IdentifierScheme,
    Instrument,
    InstrumentIdentifier,
)
from cherryfin.intelligence.retrieval import (
    KnowledgeSourceCollisionError,
    UntrustedKnowledgeInputError,
    hydrate_analysis_request,
)
from cherryfin.intelligence.store import SQLiteIntelligenceStore
from cherryfin.intelligence.tenant_store import TenantStoreRegistry
from cherryfin.security.auth import (
    AuthService,
    AuthenticationError,
    AuthorizationError,
    Role,
    Scope,
    TenantCredential,
)


def _evidence(evidence_id: str = "ev_one") -> EvidenceDocument:
    observed_at = datetime(2026, 1, 2, tzinfo=UTC)
    return EvidenceDocument(
        evidence=Evidence(
            evidence_id=evidence_id,
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name="Official Registry",
            title="Audited statements",
            observed_at=observed_at,
            data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
            trust_score=0.98,
        ),
        content="revenue=100",
    )


def _claim(evidence_id: str = "ev_one") -> FinancialClaim:
    return FinancialClaim(
        claim_id="clm_one",
        subject_id="issuer:abc",
        predicate="revenue",
        value=ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=Decimal("100"),
        ),
        unit="currency",
        currency="THB",
        effective_at=datetime(2025, 12, 31, tzinfo=UTC),
        asserted_at=datetime(2026, 1, 2, tzinfo=UTC),
        evidence_ids=[evidence_id],
        confidence=0.95,
    )


def test_production_auth_uses_configured_tenant_and_role() -> None:
    service = AuthService(
        environment="production",
        default_tenant_id="default",
        platform_admin_api_key="platform-secret",
        tenant_credentials={
            "acme": TenantCredential(
                api_key="tenant-secret",
                role=Role.ANALYST,
                actor_id="acme-agent",
            )
        },
    )
    principal = service.authenticate(
        tenant_id="ACME",
        actor_id=None,
        api_key="tenant-secret",
    )
    assert principal.tenant_id == "acme"
    assert principal.role is Role.ANALYST
    service.authorize(principal, Scope.ANALYSIS_RUN)
    with pytest.raises(AuthorizationError):
        service.authorize(principal, Scope.CLAIM_WRITE)
    with pytest.raises(AuthenticationError):
        service.authenticate(
            tenant_id="acme",
            actor_id="attacker",
            api_key="wrong-secret",
        )


def test_development_bypass_is_disabled_when_any_credentials_exist() -> None:
    service = AuthService(
        environment="development",
        default_tenant_id="default",
        platform_admin_api_key="",
        tenant_credentials={
            "acme": TenantCredential(api_key="tenant-secret", role=Role.VIEWER)
        },
    )
    with pytest.raises(AuthenticationError):
        service.authenticate(tenant_id="default", actor_id="dev", api_key=None)


def test_database_per_tenant_prevents_identifier_leakage(tmp_path) -> None:
    registry = TenantStoreRegistry(
        str(tmp_path / "intelligence.db"),
        default_tenant_id="alpha",
    )
    alpha = registry.for_tenant("alpha")
    beta = registry.for_tenant("beta")
    identifier = InstrumentIdentifier(
        scheme=IdentifierScheme.TICKER,
        value="ABC",
        venue="SET",
        primary=True,
    )
    alpha_instrument = Instrument(
        name="Alpha Company",
        asset_class=AssetClass.EQUITY,
        identifiers=[identifier],
    )
    beta_instrument = Instrument(
        name="Beta Company",
        asset_class=AssetClass.EQUITY,
        identifiers=[identifier],
    )
    alpha.add_instrument(alpha_instrument)
    beta.add_instrument(beta_instrument)

    assert (
        alpha.resolve_instrument(
            scheme=IdentifierScheme.TICKER,
            value="ABC",
            venue="SET",
        ).name
        == "Alpha Company"
    )
    assert (
        beta.resolve_instrument(
            scheme=IdentifierScheme.TICKER,
            value="ABC",
            venue="SET",
        ).name
        == "Beta Company"
    )
    registry.close_all()


def test_inline_claim_cannot_override_ledger_claim() -> None:
    store = SQLiteIntelligenceStore()
    store.add_evidence(_evidence())
    store.add_claim(_claim())
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        claims=[
            _claim().model_copy(
                update={
                    "value": ClaimValue(
                        kind=ClaimValueKind.DECIMAL,
                        decimal_value=Decimal("999999999"),
                    )
                }
            )
        ],
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            predicates=["revenue"],
            business_as_of=datetime(2026, 1, 10, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 1, 10, tzinfo=UTC),
        ),
    )
    with pytest.raises(UntrustedKnowledgeInputError):
        hydrate_analysis_request(request, store=store)


def test_inline_evidence_collision_with_ledger_is_rejected() -> None:
    store = SQLiteIntelligenceStore()
    store.add_evidence(_evidence())
    store.add_claim(_claim())
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[
            _evidence().evidence.model_copy(
                update={
                    "source_name": "Impersonated source",
                    "trust_score": 1.0,
                }
            )
        ],
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            predicates=["revenue"],
            business_as_of=datetime(2026, 1, 10, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 1, 10, tzinfo=UTC),
        ),
    )
    with pytest.raises(KnowledgeSourceCollisionError):
        hydrate_analysis_request(request, store=store)
