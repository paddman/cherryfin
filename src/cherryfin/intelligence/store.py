from __future__ import annotations

import sqlite3
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
    """Durable point-in-time store using only the Python standard library.

    The default `:memory:` path is useful for development. Production deployments should point the
    setting at a protected volume and put authorization in front of the API.
    """

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA synchronous = FULL")
        if path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteIntelligenceStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
