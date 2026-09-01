from datetime import UTC, datetime

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    CalculationTrace,
    Evidence,
    EvidenceKind,
    FinancialAnswer,
)
from cherryfin.policy.engine import PolicyEngine
from cherryfin.settings import Settings


def _answer(**updates: object) -> FinancialAnswer:
    payload: dict[str, object] = {
        "mode": AgentMode.INVESTMENT_RESEARCH,
        "summary": "Caller-provided market scenario.",
        "evidence_ids_used": ["user:note"],
        "risks": [
            {
                "code": "unverified-source",
                "level": "high",
                "message": "The source was not independently verified.",
            }
        ],
        "limitations": ["User-provided evidence only."],
        "confidence": 0.8,
        "as_of": datetime(2026, 1, 1, tzinfo=UTC),
    }
    payload.update(updates)
    return FinancialAnswer.model_validate(payload)


def test_user_provided_evidence_does_not_satisfy_verified_market_gate() -> None:
    request = AnalysisRequest(
        query="Analyze this market note",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[
            Evidence(
                evidence_id="user:note",
                kind=EvidenceKind.USER_PROVIDED,
                source_name="User",
                title="Unverified note",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                trust_score=0.6,
            )
        ],
    )

    decision = PolicyEngine(Settings()).evaluate(request=request, answer=_answer())

    assert decision.allowed is False
    assert any("verified sources" in reason for reason in decision.blocked_reasons)


def test_analysis_rejects_unregistered_model_calculation() -> None:
    request = AnalysisRequest(query="Calculate a return", mode=AgentMode.PERSONAL_CFO)
    answer = _answer(
        mode=AgentMode.PERSONAL_CFO,
        evidence_ids_used=[],
        confidence=0.3,
        calculations=[
            CalculationTrace(
                calculation_id="invented-by-model",
                name="Return",
                formula="ending / beginning - 1",
                inputs={"beginning": 100, "ending": 120},
                result=0.2,
                unit="decimal",
                deterministic=True,
            )
        ],
    )

    decision = PolicyEngine(Settings()).evaluate(request=request, answer=answer)

    assert decision.allowed is False
    assert any("calculation registry" in reason for reason in decision.blocked_reasons)
