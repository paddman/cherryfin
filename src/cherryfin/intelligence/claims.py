from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from cherryfin.core.models import ClaimStatus, ClaimValueKind, FinancialClaim
from cherryfin.intelligence.models import (
    ContradictionRecord,
    ContradictionSeverity,
    LedgerWriteResult,
)
from cherryfin.intelligence.store import (
    IntegrityConflictError,
    RecordNotFoundError,
    SQLiteIntelligenceStore,
)


class ContradictionDetector:
    """Compares claims only when they refer to the same reporting context."""

    def __init__(
        self,
        *,
        relative_tolerance: Decimal = Decimal("0.001"),
        absolute_tolerance: Decimal = Decimal("0.01"),
        material_relative_delta: Decimal = Decimal("0.05"),
    ) -> None:
        if relative_tolerance < 0 or absolute_tolerance < 0:
            raise ValueError("contradiction tolerances must be non-negative")
        if material_relative_delta < 0:
            raise ValueError("material_relative_delta must be non-negative")
        self._relative_tolerance = relative_tolerance
        self._absolute_tolerance = absolute_tolerance
        self._material_relative_delta = material_relative_delta

    def detect(
        self,
        candidate: FinancialClaim,
        existing: FinancialClaim,
    ) -> ContradictionRecord | None:
        if candidate.claim_id == existing.claim_id:
            return None
        if candidate.subject_id != existing.subject_id:
            return None
        if candidate.predicate != existing.predicate:
            return None
        if not self._same_reporting_context(candidate, existing):
            return None
        if candidate.status is ClaimStatus.RETRACTED or existing.status is ClaimStatus.RETRACTED:
            return None
        if candidate.supersedes_claim_id == existing.claim_id:
            return None
        if existing.supersedes_claim_id == candidate.claim_id:
            return None

        detected_at = max(candidate.asserted_at, existing.asserted_at)
        if candidate.value.kind is not existing.value.kind:
            return self._record(
                candidate,
                existing,
                severity=ContradictionSeverity.MATERIAL,
                reason=(
                    "Claims use different value types for the same subject, predicate, and "
                    "reporting context."
                ),
                detected_at=detected_at,
            )

        if candidate.currency != existing.currency or candidate.unit != existing.unit:
            return self._record(
                candidate,
                existing,
                severity=ContradictionSeverity.WARNING,
                reason=(
                    "Claims use different currency or unit metadata for the same reporting "
                    "context and cannot be compared safely without a deterministic conversion."
                ),
                detected_at=detected_at,
            )

        if candidate.value.kind is ClaimValueKind.DECIMAL:
            assert candidate.value.decimal_value is not None
            assert existing.value.decimal_value is not None
            left = candidate.value.decimal_value
            right = existing.value.decimal_value
            delta = abs(left - right)
            baseline = max(abs(left), abs(right))
            tolerance = max(
                self._absolute_tolerance,
                baseline * self._relative_tolerance,
            )
            if delta <= tolerance:
                return None
            relative_delta = delta / baseline if baseline else Decimal("1")
            severity = (
                ContradictionSeverity.MATERIAL
                if relative_delta >= self._material_relative_delta
                else ContradictionSeverity.WARNING
            )
            return self._record(
                candidate,
                existing,
                severity=severity,
                reason=(
                    f"Numeric values differ by {delta} ({relative_delta:.4%}) beyond the "
                    "configured tolerance."
                ),
                detected_at=detected_at,
                relative_delta=float(relative_delta),
            )

        if candidate.value.canonical() == existing.value.canonical():
            return None
        return self._record(
            candidate,
            existing,
            severity=ContradictionSeverity.WARNING,
            reason="Non-numeric values disagree for the same reporting context.",
            detected_at=detected_at,
        )

    @staticmethod
    def _same_reporting_context(left: FinancialClaim, right: FinancialClaim) -> bool:
        if left.period_start or left.period_end or right.period_start or right.period_end:
            return left.period_start == right.period_start and left.period_end == right.period_end
        return left.effective_at == right.effective_at

    @staticmethod
    def _record(
        left: FinancialClaim,
        right: FinancialClaim,
        *,
        severity: ContradictionSeverity,
        reason: str,
        detected_at: datetime,
        relative_delta: float | None = None,
    ) -> ContradictionRecord:
        claim_ids = sorted([left.claim_id, right.claim_id])
        digest = hashlib.sha256("|".join(claim_ids).encode()).hexdigest()[:24]
        return ContradictionRecord(
            contradiction_id=f"ctr_{digest}",
            claim_ids=claim_ids,
            subject_id=left.subject_id,
            predicate=left.predicate,
            severity=severity,
            reason=reason,
            relative_delta=relative_delta,
            detected_at=detected_at,
        )


class ClaimLedger:
    """Records claims and all resulting status changes atomically."""

    def __init__(
        self,
        store: SQLiteIntelligenceStore,
        detector: ContradictionDetector | None = None,
    ) -> None:
        self._store = store
        self._detector = detector or ContradictionDetector()

    def record(self, claim: FinancialClaim) -> LedgerWriteResult:
        with self._store.transaction():
            existing_claims = self._store.list_claims_for_key(
                subject_id=claim.subject_id,
                predicate=claim.predicate,
            )

            if claim.supersedes_claim_id:
                try:
                    superseded = self._store.get_claim(claim.supersedes_claim_id)
                except RecordNotFoundError as exc:
                    raise IntegrityConflictError(
                        f"supersedes_claim_id {claim.supersedes_claim_id} does not exist"
                    ) from exc
                if (
                    superseded.subject_id != claim.subject_id
                    or superseded.predicate != claim.predicate
                ):
                    raise IntegrityConflictError(
                        "a claim may only supersede another claim with the same subject "
                        "and predicate"
                    )

            stored, created = self._store.add_claim(claim)
            if not created:
                return LedgerWriteResult(claim=stored, created=False)

            contradictions: list[ContradictionRecord] = []
            for existing in existing_claims:
                if existing.status in {ClaimStatus.SUPERSEDED, ClaimStatus.RETRACTED}:
                    continue
                contradiction = self._detector.detect(stored, existing)
                if contradiction is None:
                    continue
                persisted, contradiction_created = self._store.add_contradiction(contradiction)
                if contradiction_created:
                    contradictions.append(persisted)
                    if existing.status is ClaimStatus.ACTIVE:
                        self._store.set_claim_status(
                            existing.claim_id,
                            ClaimStatus.DISPUTED,
                            changed_at=contradiction.detected_at,
                            reason=f"contradiction detected with {stored.claim_id}",
                        )

            if stored.supersedes_claim_id:
                self._store.set_claim_status(
                    stored.supersedes_claim_id,
                    ClaimStatus.SUPERSEDED,
                    changed_at=stored.asserted_at,
                    reason=f"superseded by {stored.claim_id}",
                )
            elif contradictions and stored.status is ClaimStatus.ACTIVE:
                stored = self._store.set_claim_status(
                    stored.claim_id,
                    ClaimStatus.DISPUTED,
                    changed_at=stored.asserted_at,
                    reason="contradiction detected during ledger write",
                )

            return LedgerWriteResult(
                claim=stored,
                created=True,
                contradictions=contradictions,
            )
