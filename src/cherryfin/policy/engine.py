from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from cherryfin.core.models import (
    ActionKind,
    AgentMode,
    AnalysisRequest,
    EvidenceKind,
    FinancialAnswer,
    PolicyDecision,
    SideEffectClass,
)
from cherryfin.settings import Settings

_MARKET_MODES = {
    AgentMode.INVESTMENT_RESEARCH,
    AgentMode.PORTFOLIO_RISK,
    AgentMode.TRADING_RESEARCH,
}
_EXECUTION_ACTIONS = {
    ActionKind.PLACE_ORDER,
    ActionKind.TRANSFER_FUNDS,
    ActionKind.PAY_BILL,
}
_GUARANTEE_PATTERNS = (
    r"\bguaranteed?\s+(profit|return|gain)",
    r"\brisk[- ]?free\s+(profit|return|investment)",
    r"\bcannot\s+lose\b",
    r"\b100%\s+(win|accurate|certain)\b",
    r"กำไรแน่นอน",
    r"การันตี(?:ผลตอบแทน|กำไร)",
    r"ไม่มีความเสี่ยง",
    r"ไม่มีทางขาดทุน",
    r"ชนะ\s*100%",
)
_SECRET_PATTERNS = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:api[_ -]?key|secret|password|รหัสผ่าน)\s*[:=]\s*[^\s]{8,}",
    r"\bsk-[A-Za-z0-9_-]{20,}\b",
    r"\b(?:seed phrase|mnemonic|private key)\s*[:=]\s*(?:\w+\s+){7,}\w+",
)


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    allowed: bool
    blocked_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class PolicyEngine:
    """Deterministic safety policy. Model output cannot override this class."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def preflight(self, request: AnalysisRequest) -> PreflightDecision:
        blocked: list[str] = []
        warnings: list[str] = []

        if any(
            re.search(pattern, request.query, flags=re.IGNORECASE)
            for pattern in _SECRET_PATTERNS
        ):
            blocked.append(
                "The request appears to contain a credential, private key, or recovery phrase. "
                "Remove secrets before analysis."
            )

        if request.profile and not request.sensitive_data_consent:
            warnings.append(
                "A financial profile was supplied without explicit sensitive-data consent; "
                "the request may be processed transiently but must not be persisted."
            )

        return PreflightDecision(
            allowed=not blocked,
            blocked_reasons=tuple(blocked),
            warnings=tuple(warnings),
        )

    def evaluate(
        self,
        *,
        request: AnalysisRequest,
        answer: FinancialAnswer,
        preflight_warnings: tuple[str, ...] = (),
    ) -> PolicyDecision:
        blocked: list[str] = []
        warnings: list[str] = list(preflight_warnings)
        evidence_ids = {item.evidence_id for item in request.evidence}
        used_ids = set(answer.evidence_ids_used)

        invented_ids = sorted(used_ids - evidence_ids)
        if invented_ids:
            blocked.append(
                "The answer referenced evidence IDs that were not supplied: "
                + ", ".join(invented_ids)
            )

        if answer.mode in _MARKET_MODES and not answer.evidence_ids_used:
            warnings.append(
                "Market or investment analysis has no cited evidence and must be treated as "
                "education or a scenario, not a current recommendation."
            )

        if (
            answer.mode in _MARKET_MODES
            and answer.confidence > 0.35
            and not answer.evidence_ids_used
        ):
            blocked.append("Confidence exceeds the allowed cap for uncited market analysis.")

        answer_text = " ".join(
            [answer.summary, *answer.key_findings, *answer.confidence_reasons]
        )
        if any(
            re.search(pattern, answer_text, flags=re.IGNORECASE)
            for pattern in _GUARANTEE_PATTERNS
        ):
            blocked.append("The answer contains a prohibited guarantee or risk-free claim.")

        now = datetime.now(timezone.utc)
        for evidence in request.evidence:
            data_time = evidence.data_as_of or evidence.published_at or evidence.observed_at
            data_time = self._as_utc(data_time)
            age_seconds = max(0.0, (now - data_time).total_seconds())
            if evidence.kind in {EvidenceKind.EXCHANGE, EvidenceKind.LICENSED_MARKET_DATA}:
                if age_seconds > self._settings.max_market_data_age_minutes * 60:
                    warnings.append(
                        f"Evidence {evidence.evidence_id} exceeds the configured market-data "
                        "freshness window."
                    )
            elif evidence.kind is EvidenceKind.NEWS:
                if age_seconds > self._settings.max_news_age_hours * 3600:
                    warnings.append(
                        f"Evidence {evidence.evidence_id} exceeds the configured news "
                        "freshness window."
                    )

        requires_approval = False
        for action in answer.proposed_actions:
            expected_execute = action.kind in _EXECUTION_ACTIONS
            if expected_execute and action.side_effect is not SideEffectClass.EXECUTE:
                blocked.append(
                    f"Action {action.action_id} is transactional but was not classified as execute."
                )

            if action.side_effect in {SideEffectClass.WRITE, SideEffectClass.EXECUTE}:
                requires_approval = True
                if not action.approval_required:
                    blocked.append(
                        f"Action {action.action_id} has side effects but bypasses human approval."
                    )

            if action.side_effect is SideEffectClass.EXECUTE:
                if not self._settings.execution_enabled:
                    blocked.append("Live financial execution is disabled by configuration.")
                if not action.idempotency_key:
                    blocked.append(
                        f"Executable action {action.action_id} has no idempotency key."
                    )
                if action.notional is None:
                    blocked.append(
                        f"Executable action {action.action_id} has no explicit notional."
                    )
                elif (
                    self._settings.max_transaction_notional <= 0
                    or action.notional.amount > self._settings.max_transaction_notional
                ):
                    blocked.append(
                        f"Executable action {action.action_id} exceeds the configured "
                        "notional limit."
                    )

        personalized_actions = {
            ActionKind.DEBT_PAYMENT_PLAN,
            ActionKind.PORTFOLIO_REBALANCE,
            ActionKind.PLACE_ORDER,
        }
        if request.profile is None and any(
            action.kind in personalized_actions for action in answer.proposed_actions
        ):
            warnings.append(
                "Personalized action was proposed without a financial profile; keep it in "
                "education or simulation mode."
            )

        # Analysis responses never authorize execution. A separate approval service must issue a
        # short-lived, scoped authorization before an execution connector can run.
        execution_allowed = False
        return PolicyDecision(
            allowed=not blocked,
            execution_allowed=execution_allowed,
            requires_human_approval=requires_approval,
            blocked_reasons=self._deduplicate(blocked),
            warnings=self._deduplicate(warnings),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _deduplicate(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
