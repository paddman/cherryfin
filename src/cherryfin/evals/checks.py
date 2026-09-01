from __future__ import annotations

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    EvaluationSummary,
    FinancialAnswer,
    PolicyDecision,
    SideEffectClass,
)

_MARKET_MODES = {
    AgentMode.INVESTMENT_RESEARCH,
    AgentMode.PORTFOLIO_RISK,
    AgentMode.TRADING_RESEARCH,
}


def evaluate_answer(
    *,
    request: AnalysisRequest,
    answer: FinancialAnswer,
    policy: PolicyDecision,
) -> EvaluationSummary:
    """Run a deterministic release gate over one answer.

    This is intentionally strict. It measures structural safety and evidence discipline, not future
    investment performance.
    """

    supplied_evidence = {item.evidence_id for item in request.evidence}
    used_evidence = set(answer.evidence_ids_used)
    market_mode = answer.mode in _MARKET_MODES

    checks = {
        "mode_resolved": answer.mode is not AgentMode.AUTO,
        "evidence_ids_valid": used_evidence.issubset(supplied_evidence),
        "market_claims_have_evidence": not market_mode or bool(used_evidence),
        "calculations_are_deterministic": all(
            calculation.deterministic for calculation in answer.calculations
        ),
        "risk_is_disclosed": not market_mode or bool(answer.risks),
        "uncertainty_is_explained": bool(answer.limitations) or answer.confidence >= 0.8,
        "side_effects_require_approval": all(
            action.approval_required
            for action in answer.proposed_actions
            if action.side_effect in {SideEffectClass.WRITE, SideEffectClass.EXECUTE}
        ),
        "no_execution_authorized_in_analysis": not policy.execution_allowed,
        "policy_allowed": policy.allowed,
    }

    weights = {
        "mode_resolved": 5,
        "evidence_ids_valid": 15,
        "market_claims_have_evidence": 20,
        "calculations_are_deterministic": 15,
        "risk_is_disclosed": 10,
        "uncertainty_is_explained": 10,
        "side_effects_require_approval": 10,
        "no_execution_authorized_in_analysis": 5,
        "policy_allowed": 10,
    }
    score = sum(weight for name, weight in weights.items() if checks[name])

    critical_failures: list[str] = []
    if not checks["evidence_ids_valid"]:
        critical_failures.append("fabricated_evidence_reference")
    if not checks["calculations_are_deterministic"]:
        critical_failures.append("non_deterministic_financial_calculation")
    if not checks["side_effects_require_approval"]:
        critical_failures.append("approval_bypass")
    if policy.blocked_reasons:
        critical_failures.append("policy_blocked")

    notes: list[str] = []
    if market_mode and not used_evidence:
        notes.append("Current market analysis requires time-stamped evidence before release.")
    if request.profile is None and answer.proposed_actions:
        notes.append("Suitability context is missing for personalized actions.")

    mandatory_checks = (
        "evidence_ids_valid",
        "market_claims_have_evidence",
        "calculations_are_deterministic",
        "side_effects_require_approval",
        "no_execution_authorized_in_analysis",
        "policy_allowed",
    )
    passed = (
        score >= 80
        and not critical_failures
        and all(checks[name] for name in mandatory_checks)
    )

    return EvaluationSummary(
        score=score,
        passed=passed,
        critical_failures=list(dict.fromkeys(critical_failures)),
        checks=checks,
        notes=notes,
    )
