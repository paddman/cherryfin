from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from cherryfin.agents.prompts import SYSTEM_PROMPT, build_user_prompt
from cherryfin.agents.router import route_mode
from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    AnalysisResponse,
    ClaimStatus,
    FinancialAnswer,
)
from cherryfin.evals.checks import evaluate_answer
from cherryfin.intelligence.trust import answer_has_verified_sources
from cherryfin.policy.engine import PolicyEngine
from cherryfin.providers.llm import LLMProvider
from cherryfin.settings import Settings


class UnsafeRequestError(ValueError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class AgentOutputError(RuntimeError):
    """Raised when a provider response fails the CherryFin answer contract."""


class CherryFinancialAgent:
    """Coordinates the model, deterministic policy, and answer evaluation layers."""

    def __init__(self, *, provider: LLMProvider, settings: Settings) -> None:
        self._provider = provider
        self._policy = PolicyEngine(settings)

    async def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        preflight = self._policy.preflight(request)
        if not preflight.allowed:
            raise UnsafeRequestError(preflight.blocked_reasons)

        routed_mode = route_mode(request.query, request.mode)
        result = await self._provider.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(request=request, routed_mode=routed_mode),
        )

        payload = dict(result.data)
        payload["mode"] = routed_mode
        payload.setdefault(
            "as_of",
            (request.requested_as_of or datetime.now(UTC)).isoformat(),
        )

        try:
            answer = FinancialAnswer.model_validate(payload)
        except ValidationError as exc:
            raise AgentOutputError("model output failed the FinancialAnswer schema") from exc

        answer = self._apply_confidence_caps(request=request, answer=answer)
        policy = self._policy.evaluate(
            request=request,
            answer=answer,
            preflight_warnings=preflight.warnings,
        )
        evaluation = evaluate_answer(request=request, answer=answer, policy=policy)
        return AnalysisResponse(answer=answer, policy=policy, evaluation=evaluation)

    @staticmethod
    def _apply_confidence_caps(
        *,
        request: AnalysisRequest,
        answer: FinancialAnswer,
    ) -> FinancialAnswer:
        market_modes = {
            AgentMode.INVESTMENT_RESEARCH,
            AgentMode.PORTFOLIO_RISK,
            AgentMode.TRADING_RESEARCH,
        }
        updates: dict[str, object] = {}
        limitations = list(answer.limitations)
        reasons = list(answer.confidence_reasons)
        has_sources = bool(answer.evidence_ids_used or answer.claim_ids_used)
        has_verified_sources = answer_has_verified_sources(request, answer)

        if answer.mode in market_modes and not has_verified_sources:
            updates["confidence"] = min(answer.confidence, 0.35)
            limitation = (
                "No time-stamped market evidence was supplied."
                if not has_sources
                else "Only unverified or user-provided sources were cited."
            )
            if limitation not in limitations:
                limitations.append(limitation)
            reason = (
                "Confidence is capped because current point-in-time evidence is missing."
                if not has_sources
                else "Confidence is capped because cited sources did not cross a verified boundary."
            )
            if reason not in reasons:
                reasons.append(reason)

        supplied_evidence = {item.evidence_id: item for item in request.evidence}
        supplied_claims = {item.claim_id: item for item in request.claims}
        source_scores = [
            supplied_evidence[item].trust_score
            for item in answer.evidence_ids_used
            if item in supplied_evidence
        ]
        source_scores.extend(
            supplied_claims[item].confidence
            for item in answer.claim_ids_used
            if item in supplied_claims
        )
        if source_scores:
            source_cap = min(source_scores) + 0.10
            updates["confidence"] = min(
                float(updates.get("confidence", answer.confidence)),
                source_cap,
                1.0,
            )

        disputed_claims = [
            claim_id
            for claim_id in answer.claim_ids_used
            if claim_id in supplied_claims
            and supplied_claims[claim_id].status is ClaimStatus.DISPUTED
        ]
        if disputed_claims:
            updates["confidence"] = min(
                float(updates.get("confidence", answer.confidence)),
                0.5,
            )
            limitation = "One or more cited claims are disputed: " + ", ".join(disputed_claims)
            if limitation not in limitations:
                limitations.append(limitation)
            reason = "Confidence is capped while contradictory claims await adjudication."
            if reason not in reasons:
                reasons.append(reason)

        updates["limitations"] = limitations
        updates["confidence_reasons"] = reasons
        return answer.model_copy(update=updates)
