from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.core.models import ClaimStatus, Evidence, FinancialClaim
from cherryfin.intelligence.models import KnowledgeQuery
from cherryfin.intelligence.store_common import (
    EvidenceDependencyError,
    IntegrityConflictError,
    RecordNotFoundError,
    as_utc,
    claim_fingerprint,
    iso,
    json_dumps,
)


class ClaimStoreMixin:
    def add_claim(self, claim: FinancialClaim) -> tuple[FinancialClaim, bool]:
        fingerprint = claim_fingerprint(claim)
        payload = json_dumps(claim)
        asserted_at = as_utc(claim.asserted_at)

        with self._lock, self._connection:
            by_id = self._connection.execute(
                "SELECT payload_json FROM claims WHERE claim_id = ?",
                (claim.claim_id,),
            ).fetchone()
            if by_id:
                if by_id["payload_json"] != payload:
                    raise IntegrityConflictError(
                        f"claim {claim.claim_id} already exists with different content"
                    )
                return FinancialClaim.model_validate_json(by_id["payload_json"]), False

            by_fingerprint = self._connection.execute(
                "SELECT payload_json FROM claims WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if by_fingerprint:
                return FinancialClaim.model_validate_json(by_fingerprint["payload_json"]), False

            placeholders = ",".join("?" for _ in claim.evidence_ids)
            evidence_rows = self._connection.execute(
                (
                    "SELECT evidence_id, observed_at, evidence_json FROM evidence "
                    f"WHERE evidence_id IN ({placeholders})"
                ),
                tuple(claim.evidence_ids),
            ).fetchall()
            evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
            missing = sorted(set(claim.evidence_ids) - set(evidence_by_id))
            if missing:
                raise EvidenceDependencyError(
                    "claim references evidence that is not stored: " + ", ".join(missing)
                )
            future_evidence = sorted(
                evidence_id
                for evidence_id, row in evidence_by_id.items()
                if datetime.fromisoformat(row["observed_at"]) > asserted_at
            )
            if future_evidence:
                raise EvidenceDependencyError(
                    "claim asserted_at precedes supporting evidence observed_at: "
                    + ", ".join(future_evidence)
                )
            support_trust_ceiling = min(
                Evidence.model_validate_json(row["evidence_json"]).trust_score
                for row in evidence_rows
            )
            if claim.confidence > support_trust_ceiling:
                raise EvidenceDependencyError(
                    "claim confidence exceeds the least-trusted supporting evidence"
                )

            self._connection.execute(
                """
                INSERT INTO claims(
                    claim_id, fingerprint, subject_id, predicate, effective_at, expires_at,
                    asserted_at, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.claim_id,
                    fingerprint,
                    claim.subject_id,
                    claim.predicate,
                    iso(claim.effective_at),
                    iso(claim.expires_at) if claim.expires_at else None,
                    iso(claim.asserted_at),
                    claim.status.value,
                    payload,
                ),
            )
            self._connection.executemany(
                "INSERT INTO claim_evidence(claim_id, evidence_id) VALUES (?, ?)",
                [(claim.claim_id, evidence_id) for evidence_id in claim.evidence_ids],
            )
            self._connection.execute(
                """
                INSERT INTO claim_status_history(claim_id, status, changed_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (
                    claim.claim_id,
                    claim.status.value,
                    iso(claim.asserted_at),
                    "initial claim status",
                ),
            )
        return claim, True

    def get_claim(self, claim_id: str) -> FinancialClaim:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM claims WHERE claim_id = ?",
                (claim_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"claim {claim_id} was not found")
        return FinancialClaim.model_validate_json(row["payload_json"])

    def list_claims_for_key(self, *, subject_id: str, predicate: str) -> list[FinancialClaim]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM claims
                WHERE subject_id = ? AND predicate = ?
                ORDER BY effective_at DESC, asserted_at DESC, claim_id ASC
                """,
                (subject_id, predicate),
            ).fetchall()
        return [FinancialClaim.model_validate_json(row["payload_json"]) for row in rows]

    def query_claims(self, query: KnowledgeQuery) -> list[FinancialClaim]:
        conditions = [
            "subject_id = ?",
            "effective_at <= ?",
            "asserted_at <= ?",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        knowledge_iso = iso(query.knowledge_as_of)
        parameters: list[str | int] = [
            knowledge_iso,
            query.subject_id,
            iso(query.business_as_of),
            knowledge_iso,
            iso(query.business_as_of),
        ]
        if query.predicate:
            conditions.append("predicate = ?")
            parameters.append(query.predicate)

        allowed_statuses = [ClaimStatus.ACTIVE.value]
        if query.include_disputed:
            allowed_statuses.append(ClaimStatus.DISPUTED.value)
        if query.include_superseded:
            allowed_statuses.append(ClaimStatus.SUPERSEDED.value)
        if query.include_retracted:
            allowed_statuses.append(ClaimStatus.RETRACTED.value)
        status_placeholders = ",".join("?" for _ in allowed_statuses)
        conditions.append(f"point_in_time_status IN ({status_placeholders})")
        parameters.extend(allowed_statuses)
        parameters.append(query.limit)

        sql = f"""
            WITH point_in_time AS (
                SELECT
                    c.*,
                    COALESCE(
                        (
                            SELECT h.status
                            FROM claim_status_history AS h
                            WHERE h.claim_id = c.claim_id AND h.changed_at <= ?
                            ORDER BY h.changed_at DESC
                            LIMIT 1
                        ),
                        c.status
                    ) AS point_in_time_status
                FROM claims AS c
            )
            SELECT payload_json, point_in_time_status FROM point_in_time
            WHERE {' AND '.join(conditions)}
            ORDER BY effective_at DESC, asserted_at DESC, claim_id ASC
            LIMIT ?
        """
        with self._lock:
            rows = self._connection.execute(sql, tuple(parameters)).fetchall()
        return [
            FinancialClaim.model_validate_json(row["payload_json"]).model_copy(
                update={"status": ClaimStatus(row["point_in_time_status"])}
            )
            for row in rows
        ]

    def set_claim_status(
        self,
        claim_id: str,
        status: ClaimStatus,
        *,
        changed_at: datetime | None = None,
        reason: str | None = None,
    ) -> FinancialClaim:
        claim = self.get_claim(claim_id)
        if claim.status is status:
            return claim
        updated = claim.model_copy(update={"status": status})
        effective_change_time = changed_at or datetime.now(UTC)
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE claims SET status = ?, payload_json = ? WHERE claim_id = ?",
                (status.value, json_dumps(updated), claim_id),
            )
            self._connection.execute(
                """
                INSERT OR REPLACE INTO claim_status_history(
                    claim_id, status, changed_at, reason
                ) VALUES (?, ?, ?, ?)
                """,
                (claim_id, status.value, iso(effective_change_time), reason),
            )
        return updated
