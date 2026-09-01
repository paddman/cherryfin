from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from cherryfin.agents.prompts import SYSTEM_PROMPT, build_user_prompt
from cherryfin.agents.router import route_mode
from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    AnalysisResponse,
    FinancialAnswer,
)
from cherryfin.evals.checks import evaluate_answer
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
            (request.requested_as_of or datetime.now(timezone.utc)).isoformat(),
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

        if answer.mode in market_modes and not answer.evidence_ids_used:
            updates["confidence"] = min(answer.confidence, 0.35)
            limitation = "No time-stamped market evidence was supplied."
            if limitation not in limitations:
                limitations.append(limitation)
            reason = "Confidence is capped because current evidence is missing."
            if reason not in reasons:
                reasons.append(reason)

        supplied = {item.evidence_id: item for item in request.evidence}
        used_scores = [
            supplied[item].trust_score
            for item in answer.evidence_ids_used
            if item in supplied
        ]
        if used_scores:
            evidence_cap = min(used_scores) + 0.10
            updates["confidence"] = min(
                float(updates.get("confidence", answer.confidence)),
                evidence_cap,
                1.0,
            )

        updates["limitations"] = limitations
        updates["confidence_reasons"] = reasons
        return answer.model_copy(update=updates)
