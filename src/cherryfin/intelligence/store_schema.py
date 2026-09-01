from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.intelligence.store_common import iso

_SCHEMA_VERSION = 2


class SchemaStoreMixin:
    def _initialize_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instruments (
                    instrument_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instrument_identifiers (
                    scheme TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    venue TEXT NOT NULL DEFAULT '',
                    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
                    PRIMARY KEY (scheme, normalized_value, venue)
                );

                CREATE INDEX IF NOT EXISTS idx_instrument_identifiers_instrument
                    ON instrument_identifiers(instrument_id);

                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    data_as_of TEXT,
                    published_at TEXT,
                    content_sha256 TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    content_text TEXT,
                    content_blob BLOB,
                    structured_json TEXT,
                    metadata_json TEXT NOT NULL,
                    mime_type TEXT,
                    language TEXT,
                    ingested_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_observed_at
                    ON evidence(observed_at);
                CREATE INDEX IF NOT EXISTS idx_evidence_ingested_at
                    ON evidence(ingested_at);
                CREATE INDEX IF NOT EXISTS idx_evidence_data_as_of
                    ON evidence(data_as_of);

                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    effective_at TEXT NOT NULL,
                    expires_at TEXT,
                    asserted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_claims_point_in_time
                    ON claims(subject_id, predicate, effective_at, asserted_at);

                CREATE TABLE IF NOT EXISTS claim_status_history (
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    reason TEXT,
                    PRIMARY KEY (claim_id, changed_at)
                );

                CREATE INDEX IF NOT EXISTS idx_claim_status_history_lookup
                    ON claim_status_history(claim_id, changed_at);

                CREATE TABLE IF NOT EXISTS claim_evidence (
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    PRIMARY KEY (claim_id, evidence_id)
                );

                CREATE TABLE IF NOT EXISTS contradictions (
                    contradiction_id TEXT PRIMARY KEY,
                    pair_key TEXT NOT NULL UNIQUE,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_contradictions_open
                    ON contradictions(status, subject_id, predicate);

                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    previous_hash TEXT,
                    payload_sha256 TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_events_tenant_sequence
                    ON audit_events(tenant_id, sequence);
                """
            )
            now = iso(datetime.now(UTC))
            self._connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (1, now, "baseline evidence and claims ledger"),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (2, now, "tenant-bound stores, transaction control, and audit hash chain"),
            )
            self._connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def schema_version(self) -> int:
        with self._lock:
            row = self._connection.execute("PRAGMA user_version").fetchone()
        assert row is not None
        return int(row[0])
