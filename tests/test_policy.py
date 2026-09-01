from datetime import UTC, datetime
from decimal import Decimal

from cherryfin.core.models import (
    ActionKind,
    AgentMode,
    AnalysisRequest,
    FinancialAnswer,
    Money,
    ProposedAction,
    SideEffectClass,
)
from cherryfin.policy.engine import PolicyEngine
from cherryfin.settings import Settings


def _answer(**updates: object) -> FinancialAnswer:
    payload: dict[str, object] = {
        "mode": AgentMode.PERSONAL_CFO,
        "summary": "Educational cash-flow scenario.",
        "confidence": 0.5,
        "as_of": datetime.now(UTC),
        "limitations": ["Illustrative only."],
    }
    payload.update(updates)
    return FinancialAnswer.model_validate(payload)


def test_preflight_blocks_likely_secret() -> None:
    engine = PolicyEngine(Settings())
    request = AnalysisRequest(query="password: SuperSecretPassword123")

    decision = engine.preflight(request)

    assert decision.allowed is False
    assert decision.blocked_reasons


def test_safe_educational_answer_is_allowed() -> None:
    engine = PolicyEngine(Settings())
    request = AnalysisRequest(query="Explain compound interest", mode=AgentMode.PERSONAL_CFO)

    decision = engine.evaluate(request=request, answer=_answer())

    assert decision.allowed is True
    assert decision.execution_allowed is False


def test_uncited_market_confidence_is_blocked() -> None:
    engine = PolicyEngine(Settings())
    request = AnalysisRequest(query="Should I buy this stock?", mode=AgentMode.INVESTMENT_RESEARCH)
    answer = _answer(mode=AgentMode.INVESTMENT_RESEARCH, confidence=0.9)

    decision = engine.evaluate(request=request, answer=answer)

    assert decision.allowed is False
    assert any("Confidence exceeds" in reason for reason in decision.blocked_reasons)


def test_live_order_proposal_is_never_authorized_by_analysis() -> None:
    engine = PolicyEngine(Settings(execution_enabled=False))
    request = AnalysisRequest(query="Buy an ETF", mode=AgentMode.TRADING_RESEARCH)
    action = ProposedAction(
        kind=ActionKind.PLACE_ORDER,
        title="Draft ETF order",
        description="Proposal only",
        side_effect=SideEffectClass.EXECUTE,
        notional=Money(amount=Decimal("1000"), currency="THB"),
        approval_required=True,
        idempotency_key="order-001",
    )
    answer = _answer(
        mode=AgentMode.TRADING_RESEARCH,
        confidence=0.2,
        proposed_actions=[action],
    )

    decision = engine.evaluate(request=request, answer=answer)

    assert decision.allowed is False
    assert decision.execution_allowed is False
    assert decision.requires_human_approval is True
    assert "Live financial execution is disabled by configuration." in decision.blocked_reasons
