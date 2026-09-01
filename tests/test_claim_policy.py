from datetime import UTC, datetime
from decimal import Decimal

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    ClaimStatus,
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialAnswer,
    FinancialClaim,
    KnowledgeContextRequest,
)
from cherryfin.policy.engine import PolicyEngine
from cherryfin.settings import Settings


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_one",
        kind=EvidenceKind.OFFICIAL_FILING,
        source_name="Official Registry",
        title="Annual report",
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
        data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
        trust_score=0.98,
    )


def _claim(**updates: object) -> FinancialClaim:
    payload: dict[str, object] = {
        "claim_id": "clm_one",
        "subject_id": "issuer:abc",
        "predicate": "revenue",
        "value": ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=Decimal("100"),
        ),
        "unit": "currency",
        "currency": "THB",
        "effective_at": datetime(2025, 12, 31, tzinfo=UTC),
        "asserted_at": datetime(2026, 2, 1, tzinfo=UTC),
        "evidence_ids": ["ev_one"],
        "confidence": 0.95,
    }
    payload.update(updates)
    return FinancialClaim.model_validate(payload)


def _answer(**updates: object) -> FinancialAnswer:
    payload: dict[str, object] = {
        "mode": AgentMode.INVESTMENT_RESEARCH,
        "summary": "Revenue is supported by an official filing.",
        "evidence_ids_used": ["ev_one"],
        "claim_ids_used": ["clm_one"],
        "risks": [
            {
                "code": "filing-risk",
                "level": "medium",
                "message": "The filing may later be restated.",
            }
        ],
        "limitations": ["Single reporting period."],
        "confidence": 0.8,
        "as_of": datetime(2026, 2, 2, tzinfo=UTC),
    }
    payload.update(updates)
    return FinancialAnswer.model_validate(payload)


def test_valid_claim_and_evidence_are_allowed() -> None:
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[_evidence()],
        claims=[_claim()],
    )
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=_answer())
    assert decision.allowed is True


def test_invented_claim_id_is_blocked() -> None:
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[_evidence()],
        claims=[_claim()],
    )
    answer = _answer(claim_ids_used=["clm_invented"])
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=answer)
    assert decision.allowed is False
    assert any("claim IDs" in reason for reason in decision.blocked_reasons)


def test_retracted_claim_is_blocked() -> None:
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[_evidence()],
        claims=[_claim(status=ClaimStatus.RETRACTED)],
    )
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=_answer())
    assert decision.allowed is False
    assert any("retracted" in reason for reason in decision.blocked_reasons)


def test_look_ahead_claim_is_blocked() -> None:
    request = AnalysisRequest(
        query="Backtest revenue signal",
        mode=AgentMode.TRADING_RESEARCH,
        evidence=[_evidence()],
        claims=[_claim(asserted_at=datetime(2026, 2, 10, tzinfo=UTC))],
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            knowledge_as_of=datetime(2026, 2, 5, tzinfo=UTC),
        ),
    )
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=_answer())
    assert decision.allowed is False
    assert any("knowledge_as_of" in reason for reason in decision.blocked_reasons)


def test_direct_evidence_look_ahead_is_blocked() -> None:
    request = AnalysisRequest(
        query="Backtest revenue signal",
        mode=AgentMode.TRADING_RESEARCH,
        evidence=[
            _evidence().model_copy(update={"observed_at": datetime(2026, 2, 10, tzinfo=UTC)})
        ],
        claims=[],
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            knowledge_as_of=datetime(2026, 2, 5, tzinfo=UTC),
        ),
    )
    answer = _answer(claim_ids_used=[])
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=answer)
    assert decision.allowed is False
    assert any("evidence not yet known" in reason for reason in decision.blocked_reasons)


def test_expired_claim_is_blocked_for_business_time() -> None:
    request = AnalysisRequest(
        query="Analyze revenue",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[_evidence()],
        claims=[_claim(expires_at=datetime(2026, 2, 15, tzinfo=UTC))],
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            business_as_of=datetime(2026, 2, 15, tzinfo=UTC),
        ),
    )
    decision = PolicyEngine(Settings()).evaluate(request=request, answer=_answer())
    assert decision.allowed is False
    assert any("expired at business_as_of" in reason for reason in decision.blocked_reasons)
