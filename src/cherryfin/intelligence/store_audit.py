from __future__ import annotations

import hashlib

from cherryfin.intelligence.store_common import json_dumps


class AuditStoreMixin:
    def snapshot_sha256(self) -> str:
        sections: list[str] = []
        with self._lock:
            for table, columns, order_by in (
                ("instruments", "instrument_id, payload_json", "instrument_id"),
                (
                    "evidence",
                    "evidence_id, content_sha256, evidence_json",
                    "evidence_id",
                ),
                (
                    "claims",
                    "claim_id, fingerprint, status, payload_json",
                    "claim_id",
                ),
                (
                    "claim_status_history",
                    "claim_id, status, changed_at, reason",
                    "claim_id, changed_at",
                ),
                (
                    "contradictions",
                    "contradiction_id, pair_key, status, payload_json",
                    "contradiction_id",
                ),
            ):
                rows = self._connection.execute(
                    f"SELECT {columns} FROM {table} ORDER BY {order_by}"
                ).fetchall()
                sections.append(table)
                sections.extend(json_dumps(dict(row)) for row in rows)
        return hashlib.sha256("\n".join(sections).encode()).hexdigest()
