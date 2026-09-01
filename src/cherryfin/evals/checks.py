from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    EvaluationSummary,
    FinancialAnswer,
    PolicyDecision,
    SideEffectClass,
)
from cherryfin.intelligence.trust import answer_has_verified_sources

_MARKET_MODES = {
    AgentMode.INVESTMENT_RESEARCH,
    AgentMode.PORTFOLIO_RISK,
    AgentMode.TRADING_RESEARCH,
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def evaluate_answer(
    *,
    request: AnalysisRequest,
    answer: FinancialAnswer,
    policy: PolicyDecision,
) -> EvaluationSummary:
    """Run a deterministic release gate over one answer.

    This measures structural safety and evidence discipline, not future investment performance.
    """

    evidence_by_id = {item.evidence_id: item for item in request.evidence}
    supplied_evidence = set(evidence_by_id)
    claims_by_id = {item.claim_id: item for item in request.claims}
    supplied_claims = set(claims_by_id)
    used_evidence = set(answer.evidence_ids_used)
    used_claims = set(answer.claim_ids_used)
    market_mode = answer.mode in _MARKET_MODES

    claim_support_complete = all(
        set(claims_by_id[claim_id].evidence_ids).issubset(supplied_evidence)
        for claim_id in used_claims
        if claim_id in claims_by_id
    )
    business_as_of = request.requested_as_of
    knowledge_as_of = None
    if request.knowledge_context:
        business_as_of = request.knowledge_context.business_as_of or business_as_of
        knowledge_as_of = request.knowledge_context.knowledge_as_of
    business_cutoff = _as_utc(business_as_of) if business_as_of else None
    knowledge_cutoff = _as_utc(knowledge_as_of) if knowledge_as_of else None

    used_source_ids = set(used_evidence)
    for claim_id in used_claims & set(claims_by_id):
        used_source_ids.update(claims_by_id[claim_id].evidence_ids)

    claims_point_in_time_safe = all(
        (
            business_cutoff is None
            or (
                _as_utc(claims_by_id[claim_id].effective_at) <= business_cutoff
                and (
                    claims_by_id[claim_id].expires_at is None
                    or _as_utc(claims_by_id[claim_id].expires_at) > business_cutoff
                )
            )
        )
        and (
            knowledge_cutoff is None
            or _as_utc(claims_by_id[claim_id].asserted_at) <= knowledge_cutoff
        )
        for claim_id in used_claims
        if claim_id in claims_by_id
    )
    evidence_point_in_time_safe = all(
        (
            business_cutoff is None
            or _as_utc(
                evidence_by_id[evidence_id].data_as_of
                or evidence_by_id[evidence_id].published_at
                or evidence_by_id[evidence_id].observed_at
            )
            <= business_cutoff
        )
        and (
            knowledge_cutoff is None
            or _as_utc(evidence_by_id[evidence_id].observed_at) <= knowledge_cutoff
        )
        for evidence_id in used_source_ids
        if evidence_id in evidence_by_id
    )
    point_in_time_safe = claims_point_in_time_safe and evidence_point_in_time_safe
    verified_sources = answer_has_verified_sources(request, answer)

    checks = {
        "mode_resolved": answer.mode is not AgentMode.AUTO,
        "evidence_ids_valid": used_evidence.issubset(supplied_evidence),
        "claim_ids_valid": used_claims.issubset(supplied_claims),
        "claim_support_complete": claim_support_complete,
        "point_in_time_safe": point_in_time_safe,
        "market_claims_have_verified_sources": not market_mode or verified_sources,
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
        "evidence_ids_valid": 10,
        "claim_ids_valid": 10,
        "claim_support_complete": 10,
        "point_in_time_safe": 10,
        "market_claims_have_verified_sources": 15,
        "calculations_are_deterministic": 10,
        "risk_is_disclosed": 5,
        "uncertainty_is_explained": 5,
        "side_effects_require_approval": 5,
        "no_execution_authorized_in_analysis": 5,
        "policy_allowed": 10,
    }
    score = sum(weight for name, weight in weights.items() if checks[name])

    critical_failures: list[str] = []
    if not checks["evidence_ids_valid"]:
        critical_failures.append("fabricated_evidence_reference")
    if not checks["claim_ids_valid"]:
        critical_failures.append("fabricated_claim_reference")
    if not checks["claim_support_complete"]:
        critical_failures.append("claim_without_supplied_evidence")
    if not checks["point_in_time_safe"]:
        critical_failures.append("point_in_time_violation")
    if not checks["calculations_are_deterministic"]:
        critical_failures.append("non_deterministic_financial_calculation")
    if not checks["side_effects_require_approval"]:
        critical_failures.append("approval_bypass")
    if policy.blocked_reasons:
        critical_failures.append("policy_blocked")

    notes: list[str] = []
    if market_mode and not verified_sources:
        notes.append(
            "Current market analysis requires a verified, time-stamped source before release."
        )
    if request.profile is None and answer.proposed_actions:
        notes.append("Suitability context is missing for personalized actions.")

    mandatory_checks = (
        "evidence_ids_valid",
        "claim_ids_valid",
        "claim_support_complete",
        "point_in_time_safe",
        "market_claims_have_verified_sources",
        "calculations_are_deterministic",
        "side_effects_require_approval",
        "no_execution_authorized_in_analysis",
        "policy_allowed",
    )
    passed = (
        score >= 80 and not critical_failures and all(checks[name] for name in mandatory_checks)
    )

    return EvaluationSummary(
        score=score,
        passed=passed,
        critical_failures=list(dict.fromkeys(critical_failures)),
        checks=checks,
        notes=notes,
    )
