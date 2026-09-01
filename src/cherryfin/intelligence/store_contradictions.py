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
        with self._write():
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
            self.append_audit_event(
                action="contradiction.created",
                resource_type="contradiction",
                resource_id=contradiction.contradiction_id,
                payload={
                    "claim_ids": contradiction.claim_ids,
                    "subject_id": contradiction.subject_id,
                    "predicate": contradiction.predicate,
                    "severity": contradiction.severity.value,
                },
                occurred_at=contradiction.detected_at,
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
        with self.transaction():
            row = self._connection.execute(
                "SELECT payload_json FROM contradictions WHERE contradiction_id = ?",
                (contradiction_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFoundError(f"contradiction {contradiction_id} was not found")
            contradiction = ContradictionRecord.model_validate_json(row["payload_json"])

            target_status = (
                ContradictionStatus.DISMISSED if dismiss else ContradictionStatus.RESOLVED
            )
            if contradiction.status is not ContradictionStatus.OPEN:
                if (
                    contradiction.status is target_status
                    and contradiction.accepted_claim_id == accepted_claim_id
                    and contradiction.resolution_note == resolution_note
                ):
                    return contradiction
                raise IntegrityConflictError("contradiction has already been resolved")

            if dismiss:
                if accepted_claim_id is not None:
                    raise IntegrityConflictError(
                        "accepted_claim_id must be omitted when dismissing a contradiction"
                    )
            else:
                if accepted_claim_id is None or accepted_claim_id not in contradiction.claim_ids:
                    raise IntegrityConflictError(
                        "accepted_claim_id must be one of the contradiction claim_ids"
                    )
                for claim_id in contradiction.claim_ids:
                    claim_status = (
                        ClaimStatus.ACTIVE
                        if claim_id == accepted_claim_id
                        else ClaimStatus.SUPERSEDED
                    )
                    self.set_claim_status(
                        claim_id,
                        claim_status,
                        reason=f"contradiction {contradiction_id} resolved",
                    )

            resolved_at = datetime.now(UTC)
            updated = contradiction.model_copy(
                update={
                    "status": target_status,
                    "resolved_at": resolved_at,
                    "accepted_claim_id": accepted_claim_id,
                    "resolution_note": resolution_note,
                }
            )
            self._connection.execute(
                """
                UPDATE contradictions
                SET status = ?, payload_json = ?
                WHERE contradiction_id = ?
                """,
                (updated.status.value, json_dumps(updated), contradiction_id),
            )
            self.append_audit_event(
                action="contradiction.resolved",
                resource_type="contradiction",
                resource_id=contradiction_id,
                payload={
                    "status": target_status.value,
                    "accepted_claim_id": accepted_claim_id,
                    "resolution_note": resolution_note,
                },
                occurred_at=resolved_at,
            )
        return updated
