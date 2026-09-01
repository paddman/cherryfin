from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from cherryfin.intelligence.store_audit import AuditStoreMixin
from cherryfin.intelligence.store_claims import ClaimStoreMixin
from cherryfin.intelligence.store_common import (
    EvidenceDependencyError,
    IdentifierConflictError,
    IntegrityConflictError,
    IntelligenceStoreError,
    RecordNotFoundError,
)
from cherryfin.intelligence.store_contradictions import ContradictionStoreMixin
from cherryfin.intelligence.store_evidence import EvidenceStoreMixin
from cherryfin.intelligence.store_instruments import InstrumentStoreMixin
from cherryfin.intelligence.store_schema import SchemaStoreMixin
from cherryfin.security.auth import normalize_tenant_id

__all__ = [
    "EvidenceDependencyError",
    "IdentifierConflictError",
    "IntegrityConflictError",
    "IntelligenceStoreError",
    "RecordNotFoundError",
    "SQLiteIntelligenceStore",
]


class SQLiteIntelligenceStore(
    SchemaStoreMixin,
    InstrumentStoreMixin,
    EvidenceStoreMixin,
    ClaimStoreMixin,
    ContradictionStoreMixin,
    AuditStoreMixin,
):
    """Tenant-bound point-in-time store with explicit transaction control."""

    def __init__(
        self,
        path: str = ":memory:",
        *,
        tenant_id: str = "default",
        trust_client_timestamps: bool = True,
    ) -> None:
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.tenant_id = normalize_tenant_id(tenant_id)
        self.trust_client_timestamps = trust_client_timestamps
        self._lock = RLock()
        self._transaction_depth = 0
        self._transaction_failed = False
        self._closed = False
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA synchronous = FULL")
        if path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    @contextmanager
    def transaction(self) -> Iterator[SQLiteIntelligenceStore]:
        """Run all nested writes as one BEGIN IMMEDIATE transaction."""

        with self._lock:
            if self._closed:
                raise IntelligenceStoreError("intelligence store is closed")
            outermost = self._transaction_depth == 0
            if outermost:
                self._connection.execute("BEGIN IMMEDIATE")
                self._transaction_failed = False
            self._transaction_depth += 1
            try:
                yield self
            except Exception:
                self._transaction_failed = True
                raise
            finally:
                self._transaction_depth -= 1
                if outermost:
                    try:
                        if self._transaction_failed:
                            self._connection.rollback()
                        else:
                            self._connection.commit()
                    finally:
                        self._transaction_failed = False

    @contextmanager
    def _write(self) -> Iterator[None]:
        if self._transaction_depth:
            yield
            return
        with self.transaction():
            yield

    def record_count(self, table: str) -> int:
        allowed = {
            "instruments",
            "evidence",
            "claims",
            "contradictions",
            "audit_events",
        }
        if table not in allowed:
            raise ValueError(f"unsupported record-count table: {table}")
        with self._lock:
            row = self._connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        assert row is not None
        return int(row["count"])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> SQLiteIntelligenceStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
