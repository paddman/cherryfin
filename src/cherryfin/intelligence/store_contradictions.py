from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.core.models import ClaimStatus
from cherryfin.intelligence.models import ContradictionRecord, ContradictionStatus
from cherryfin.intelligence.store_common import (
    IntegrityConflictError,
    RecordNotFoundError,
    iso,
    json_dumps,
)


class ContradictionStoreMixin:
    def add_contradiction(
        self,
        contradiction: ContradictionRecord,
    ) -> tuple[ContradictionRecord, bool]:
        pair_key = "|".join(contradiction.claim_ids)
        payload = json_dumps(contradiction)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload_json FROM contradictions WHERE pair_key = ?",
                (pair_key,),
            ).fetchone()
            if existing:
                return ContradictionRecord.model_validate_json(existing["payload_json"]), False
            self._connection.execute(
                """
                INSERT INTO contradictions(
                    contradiction_id, pair_key, subject_id, predicate, severity, status,
                    detected_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contradiction.contradiction_id,
                    pair_key,
                    contradiction.subject_id,
                    contradiction.predicate,
                    contradiction.severity.value,
                    contradiction.status.value,
                    iso(contradiction.detected_at),
                    payload,
                ),
            )
        return contradiction, True

    def list_contradictions(
        self,
        *,
        status: ContradictionStatus | None = ContradictionStatus.OPEN,
        subject_id: str | None = None,
        limit: int = 500,
    ) -> list[ContradictionRecord]:
        conditions: list[str] = []
        parameters: list[str | int] = []
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status.value)
        if subject_id is not None:
            conditions.append("subject_id = ?")
            parameters.append(subject_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(max(1, min(limit, 5000)))
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT payload_json FROM contradictions
                {where}
                ORDER BY detected_at DESC, contradiction_id ASC
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [ContradictionRecord.model_validate_json(row["payload_json"]) for row in rows]

    def resolve_contradiction(
        self,
        *,
        contradiction_id: str,
        accepted_claim_id: str | None,
        resolution_note: str,
        dismiss: bool = False,
    ) -> ContradictionRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM contradictions WHERE contradiction_id = ?",
                (contradiction_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"contradiction {contradiction_id} was not found")
        contradiction = ContradictionRecord.model_validate_json(row["payload_json"])
        if not dismiss:
            if accepted_claim_id is None or accepted_claim_id not in contradiction.claim_ids:
                raise IntegrityConflictError(
                    "accepted_claim_id must be one of the contradiction claim_ids"
                )
            for claim_id in contradiction.claim_ids:
                target_status = (
                    ClaimStatus.ACTIVE
                    if claim_id == accepted_claim_id
                    else ClaimStatus.SUPERSEDED
                )
                self.set_claim_status(claim_id, target_status)

        updated = contradiction.model_copy(
            update={
                "status": (
                    ContradictionStatus.DISMISSED
                    if dismiss
                    else ContradictionStatus.RESOLVED
                ),
                "resolved_at": datetime.now(UTC),
                "accepted_claim_id": accepted_claim_id,
                "resolution_note": resolution_note,
            }
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE contradictions
                SET status = ?, payload_json = ?
                WHERE contradiction_id = ?
                """,
                (updated.status.value, json_dumps(updated), contradiction_id),
            )
        return updated
