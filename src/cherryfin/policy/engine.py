from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from cherryfin.core.models import (
    ActionKind,
    AgentMode,
    AnalysisRequest,
    ClaimStatus,
    Evidence,
    EvidenceKind,
    FinancialAnswer,
    PolicyDecision,
    SideEffectClass,
)
from cherryfin.intelligence.trust import answer_has_verified_sources
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

        untrusted_values = [request.query, *request.metadata.values()]
        for evidence in request.evidence:
            if evidence.kind is EvidenceKind.USER_PROVIDED:
                untrusted_values.extend(
                    [
                        evidence.source_name,
                        evidence.title,
                        evidence.uri or "",
                        evidence.excerpt or "",
                    ]
                )
        if any(self._contains_secret(value) for value in untrusted_values):
            blocked.append(
                "The request appears to contain a credential, private key, or recovery phrase. "
                "Remove secrets before analysis."
            )

        if request.profile and not request.sensitive_data_consent:
            warnings.append(
                "A financial profile was supplied without explicit sensitive-data consent; "
                "the request may be processed transiently but must not be persisted."
            )

        evidence_ids = [item.evidence_id for item in request.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            blocked.append("The request contains duplicate evidence IDs.")
        claim_ids = [item.claim_id for item in request.claims]
        if len(claim_ids) != len(set(claim_ids)):
            blocked.append("The request contains duplicate claim IDs.")

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
        evidence_by_id = {item.evidence_id: item for item in request.evidence}
        claims_by_id = {item.claim_id: item for item in request.claims}
        used_evidence_ids = set(answer.evidence_ids_used)
        used_claim_ids = set(answer.claim_ids_used)

        invented_evidence_ids = sorted(used_evidence_ids - set(evidence_by_id))
        if invented_evidence_ids:
            blocked.append(
                "The answer referenced evidence IDs that were not supplied: "
                + ", ".join(invented_evidence_ids)
            )
        invented_claim_ids = sorted(used_claim_ids - set(claims_by_id))
        if invented_claim_ids:
            blocked.append(
                "The answer referenced claim IDs that were not supplied: "
                + ", ".join(invented_claim_ids)
            )

        for claim_id in sorted(used_claim_ids & set(claims_by_id)):
            claim = claims_by_id[claim_id]
            missing_support = sorted(set(claim.evidence_ids) - set(evidence_by_id))
            if missing_support:
                blocked.append(
                    f"Claim {claim_id} lacks supplied supporting evidence: "
                    + ", ".join(missing_support)
                )
            future_support = sorted(
                evidence_id
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
                and self._as_utc(evidence_by_id[evidence_id].observed_at)
                > self._as_utc(claim.asserted_at)
            )
            if future_support:
                blocked.append(
                    f"Claim {claim_id} predates supporting evidence: " + ", ".join(future_support)
                )
            if claim.status is ClaimStatus.RETRACTED:
                blocked.append(f"The answer cited retracted claim {claim_id}.")
            elif claim.status is ClaimStatus.SUPERSEDED:
                warnings.append(f"The answer cited superseded claim {claim_id}.")
            elif claim.status is ClaimStatus.DISPUTED:
                warnings.append(
                    f"The answer cited disputed claim {claim_id}; confidence must remain capped."
                )

        business_as_of = request.requested_as_of
        knowledge_as_of = None
        if request.knowledge_context:
            business_as_of = request.knowledge_context.business_as_of or business_as_of
            knowledge_as_of = request.knowledge_context.knowledge_as_of

        if business_as_of:
            business_cutoff = self._as_utc(business_as_of)
            future_claim_ids = sorted(
                claim_id
                for claim_id in used_claim_ids
                if claim_id in claims_by_id
                and self._as_utc(claims_by_id[claim_id].effective_at) > business_cutoff
            )
            expired_claim_ids = sorted(
                claim_id
                for claim_id in used_claim_ids
                if claim_id in claims_by_id
                and claims_by_id[claim_id].expires_at is not None
                and self._as_utc(claims_by_id[claim_id].expires_at) <= business_cutoff
            )
            future_evidence_ids = sorted(
                evidence_id
                for evidence_id in used_evidence_ids
                if evidence_id in evidence_by_id
                and self._evidence_business_time(evidence_by_id[evidence_id]) > business_cutoff
            )
            if future_claim_ids:
                blocked.append(
                    "The answer used claims effective after business_as_of: "
                    + ", ".join(future_claim_ids)
                )
            if expired_claim_ids:
                blocked.append(
                    "The answer used claims expired at business_as_of: "
                    + ", ".join(expired_claim_ids)
                )
            if future_evidence_ids:
                blocked.append(
                    "The answer used evidence dated after business_as_of: "
                    + ", ".join(future_evidence_ids)
                )

        if knowledge_as_of:
            knowledge_cutoff = self._as_utc(knowledge_as_of)
            look_ahead_claim_ids = sorted(
                claim_id
                for claim_id in used_claim_ids
                if claim_id in claims_by_id
                and self._as_utc(claims_by_id[claim_id].asserted_at) > knowledge_cutoff
            )
            look_ahead_evidence_ids = sorted(
                evidence_id
                for evidence_id in used_evidence_ids
                if evidence_id in evidence_by_id
                and self._as_utc(evidence_by_id[evidence_id].observed_at) > knowledge_cutoff
            )
            if look_ahead_claim_ids:
                blocked.append(
                    "The answer used claims not yet known at knowledge_as_of: "
                    + ", ".join(look_ahead_claim_ids)
                )
            if look_ahead_evidence_ids:
                blocked.append(
                    "The answer used evidence not yet known at knowledge_as_of: "
                    + ", ".join(look_ahead_evidence_ids)
                )

        verified_sources = answer_has_verified_sources(request, answer)
        if answer.mode in _MARKET_MODES and not verified_sources:
            warnings.append(
                "Market or investment analysis lacks a verified source and must be treated as "
                "education or a scenario, not a current recommendation."
            )
        if answer.mode in _MARKET_MODES and answer.confidence > 0.35 and not verified_sources:
            blocked.append(
                "Confidence exceeds the allowed cap for market analysis without verified sources."
            )

        if answer.calculations:
            blocked.append(
                "Analysis returned calculations without a server calculation registry artifact."
            )

        answer_text = " ".join([answer.summary, *answer.key_findings, *answer.confidence_reasons])
        if any(
            re.search(pattern, answer_text, flags=re.IGNORECASE) for pattern in _GUARANTEE_PATTERNS
        ):
            blocked.append("The answer contains a prohibited guarantee or risk-free claim.")

        relevant_evidence_ids = set(used_evidence_ids)
        for claim_id in used_claim_ids & set(claims_by_id):
            relevant_evidence_ids.update(claims_by_id[claim_id].evidence_ids)

        now = datetime.now(UTC)
        for evidence_id in sorted(relevant_evidence_ids & set(evidence_by_id)):
            evidence = evidence_by_id[evidence_id]
            data_time = evidence.data_as_of or evidence.published_at or evidence.observed_at
            data_time = self._as_utc(data_time)
            age_seconds = max(0.0, (now - data_time).total_seconds())
            if evidence.kind in {EvidenceKind.EXCHANGE, EvidenceKind.LICENSED_MARKET_DATA}:
                if age_seconds > self._settings.max_market_data_age_minutes * 60:
                    warnings.append(
                        f"Evidence {evidence.evidence_id} exceeds the configured market-data "
                        "freshness window."
                    )
            elif (
                evidence.kind is EvidenceKind.NEWS
                and age_seconds > self._settings.max_news_age_hours * 3600
            ):
                warnings.append(
                    f"Evidence {evidence.evidence_id} exceeds the configured news freshness window."
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
                    blocked.append(f"Executable action {action.action_id} has no idempotency key.")
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

        return PolicyDecision(
            allowed=not blocked,
            execution_allowed=False,
            requires_human_approval=requires_approval,
            blocked_reasons=self._deduplicate(blocked),
            warnings=self._deduplicate(warnings),
        )

    def _contains_secret(self, value: str) -> bool:
        return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in _SECRET_PATTERNS)

    def _evidence_business_time(self, evidence: Evidence) -> datetime:
        data_as_of = evidence.data_as_of
        published_at = evidence.published_at
        observed_at = evidence.observed_at
        return self._as_utc(data_as_of or published_at or observed_at)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _deduplicate(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))
