from __future__ import annotations

import hashlib
from pathlib import Path
from threading import RLock

from cherryfin.intelligence.store import SQLiteIntelligenceStore
from cherryfin.security.auth import normalize_tenant_id


class TenantStoreRegistry:
    """Provides one SQLite database per tenant.

    Database-per-tenant isolation avoids accidental cross-tenant SQL reads while CherryFin uses
    SQLite. A future PostgreSQL backend can implement row-level security behind the same boundary.
    """

    def __init__(
        self,
        base_path: str,
        *,
        default_tenant_id: str = "default",
        trust_client_timestamps: bool = False,
    ) -> None:
        self._base_path = base_path
        self._default_tenant_id = normalize_tenant_id(default_tenant_id)
        self._trust_client_timestamps = trust_client_timestamps
        self._lock = RLock()
        self._stores: dict[str, SQLiteIntelligenceStore] = {}

    def for_tenant(self, tenant_id: str) -> SQLiteIntelligenceStore:
        normalized = normalize_tenant_id(tenant_id)
        with self._lock:
            existing = self._stores.get(normalized)
            if existing is not None:
                return existing
            store = SQLiteIntelligenceStore(
                self._path_for_tenant(normalized),
                tenant_id=normalized,
                trust_client_timestamps=self._trust_client_timestamps,
            )
            self._stores[normalized] = store
            return store

    def close_all(self) -> None:
        with self._lock:
            stores = list(self._stores.values())
            self._stores.clear()
        for store in stores:
            store.close()

    def _path_for_tenant(self, tenant_id: str) -> str:
        if self._base_path == ":memory:":
            return ":memory:"

        configured = Path(self._base_path).expanduser()
        if tenant_id == self._default_tenant_id and configured.suffix:
            configured.resolve().parent.mkdir(parents=True, exist_ok=True)
            return str(configured)

        if configured.suffix:
            root = configured.parent / f"{configured.stem}_tenants"
        else:
            root = configured
        root.resolve().mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(tenant_id.encode()).hexdigest()
        return str(root / f"{digest}.db")
