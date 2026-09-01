from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from cherryfin.intelligence.store_common import iso, json_dumps

_actor_id: ContextVar[str] = ContextVar("cherryfin_audit_actor_id", default="system")
_request_id: ContextVar[str] = ContextVar("cherryfin_audit_request_id", default="internal")


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    event_id: str
    tenant_id: str
    occurred_at: datetime
    actor_id: str
    request_id: str
    action: str
    resource_type: str
    resource_id: str
    previous_hash: str | None = None
    payload_sha256: str = Field(min_length=64, max_length=64)
    event_hash: str = Field(min_length=64, max_length=64)
    payload: dict[str, Any]


class AuditVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    events_checked: int = Field(ge=0)
    last_event_hash: str | None = None
    errors: list[str] = Field(default_factory=list)


class AuditStoreMixin:
    @contextmanager
    def audit_context(self, *, actor_id: str, request_id: str) -> Iterator[None]:
        actor_token = _actor_id.set(actor_id)
        request_token = _request_id.set(request_id)
        try:
            yield
        finally:
            _request_id.reset(request_token)
            _actor_id.reset(actor_token)

    def append_audit_event(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        event_id = str(uuid4())
        event_time = occurred_at or datetime.now(UTC)
        actor_id = _actor_id.get()
        request_id = _request_id.get()
        payload_json = json_dumps(payload or {})
        payload_sha256 = hashlib.sha256(payload_json.encode()).hexdigest()

        with self._write():
            previous = self._connection.execute(
                """
                SELECT event_hash FROM audit_events
                WHERE tenant_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (self.tenant_id,),
            ).fetchone()
            previous_hash = previous["event_hash"] if previous else None
            material = {
                "event_id": event_id,
                "tenant_id": self.tenant_id,
                "occurred_at": iso(event_time),
                "actor_id": actor_id,
                "request_id": request_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "previous_hash": previous_hash,
                "payload_sha256": payload_sha256,
            }
            event_hash = hashlib.sha256(json_dumps(material).encode()).hexdigest()
            cursor = self._connection.execute(
                """
                INSERT INTO audit_events(
                    event_id, tenant_id, occurred_at, actor_id, request_id, action,
                    resource_type, resource_id, previous_hash, payload_sha256,
                    event_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    self.tenant_id,
                    iso(event_time),
                    actor_id,
                    request_id,
                    action,
                    resource_type,
                    resource_id,
                    previous_hash,
                    payload_sha256,
                    event_hash,
                    payload_json,
                ),
            )
            sequence = int(cursor.lastrowid)

        return AuditEvent(
            sequence=sequence,
            event_id=event_id,
            tenant_id=self.tenant_id,
            occurred_at=event_time,
            actor_id=actor_id,
            request_id=request_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            previous_hash=previous_hash,
            payload_sha256=payload_sha256,
            event_hash=event_hash,
            payload=payload or {},
        )

    def list_audit_events(self, *, limit: int = 200) -> list[AuditEvent]:
        bounded_limit = max(1, min(limit, 5000))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_events
                WHERE tenant_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (self.tenant_id, bounded_limit),
            ).fetchall()
        return [self._audit_event_from_row(row) for row in rows]

    def verify_audit_chain(self) -> AuditVerification:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_events
                WHERE tenant_id = ?
                ORDER BY sequence ASC
                """,
                (self.tenant_id,),
            ).fetchall()

        errors: list[str] = []
        expected_previous: str | None = None
        for row in rows:
            payload_json = row["payload_json"]
            payload_sha256 = hashlib.sha256(payload_json.encode()).hexdigest()
            if payload_sha256 != row["payload_sha256"]:
                errors.append(f"event {row['event_id']} payload hash mismatch")
            material = {
                "event_id": row["event_id"],
                "tenant_id": row["tenant_id"],
                "occurred_at": row["occurred_at"],
                "actor_id": row["actor_id"],
                "request_id": row["request_id"],
                "action": row["action"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "previous_hash": row["previous_hash"],
                "payload_sha256": row["payload_sha256"],
            }
            calculated = hashlib.sha256(json_dumps(material).encode()).hexdigest()
            if row["previous_hash"] != expected_previous:
                errors.append(f"event {row['event_id']} previous hash mismatch")
            if row["event_hash"] != calculated:
                errors.append(f"event {row['event_id']} event hash mismatch")
            expected_previous = row["event_hash"]

        return AuditVerification(
            valid=not errors,
            events_checked=len(rows),
            last_event_hash=expected_previous,
            errors=errors,
        )

    def snapshot_sha256(self) -> str:
        verification = self.verify_audit_chain()
        counts = {
            table: self.record_count(table)
            for table in ("instruments", "evidence", "claims", "contradictions")
        }
        snapshot = {
            "tenant_id": self.tenant_id,
            "schema_version": self.schema_version(),
            "counts": counts,
            "audit_valid": verification.valid,
            "audit_events": verification.events_checked,
            "last_event_hash": verification.last_event_hash,
        }
        return hashlib.sha256(json_dumps(snapshot).encode()).hexdigest()

    @staticmethod
    def _audit_event_from_row(row: Any) -> AuditEvent:
        return AuditEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            tenant_id=row["tenant_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            actor_id=row["actor_id"],
            request_id=row["request_id"],
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            previous_hash=row["previous_hash"],
            payload_sha256=row["payload_sha256"],
            event_hash=row["event_hash"],
            payload=json.loads(row["payload_json"]),
        )
