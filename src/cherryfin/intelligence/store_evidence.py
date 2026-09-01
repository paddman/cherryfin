from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime

from cherryfin.core.models import Evidence
from cherryfin.intelligence.models import EvidenceDocument
from cherryfin.intelligence.store_common import (
    IntegrityConflictError,
    RecordNotFoundError,
    iso,
    json_dumps,
)


class EvidenceStoreMixin:
    def add_evidence(self, document: EvidenceDocument) -> tuple[EvidenceDocument, bool]:
        canonical_material: bytes | None = None
        if document.content is not None:
            canonical_material = document.content.encode()
        elif document.content_bytes is not None:
            canonical_material = document.content_bytes
        elif document.structured_payload is not None:
            canonical_material = json_dumps(document.structured_payload).encode()

        supplied_hash = document.evidence.content_sha256
        computed_hash = (
            hashlib.sha256(canonical_material).hexdigest() if canonical_material else None
        )
        if supplied_hash and computed_hash and supplied_hash != computed_hash:
            raise IntegrityConflictError(
                "supplied evidence content_sha256 does not match the ingested content"
            )
        content_hash = supplied_hash or computed_hash
        if content_hash is None:
            raise IntegrityConflictError("evidence has no verifiable content hash")

        evidence = document.evidence.model_copy(update={"content_sha256": content_hash})
        stored_document = document.model_copy(update={"evidence": evidence})
        evidence_json = json_dumps(evidence)
        structured_json = (
            json_dumps(stored_document.structured_payload)
            if stored_document.structured_payload is not None
            else None
        )
        metadata_json = json_dumps(stored_document.metadata)

        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if existing:
                immutable_existing = (
                    existing["content_sha256"],
                    existing["evidence_json"],
                    existing["content_text"],
                    existing["content_blob"],
                    existing["structured_json"],
                    existing["metadata_json"],
                    existing["mime_type"],
                    existing["language"],
                )
                immutable_candidate = (
                    content_hash,
                    evidence_json,
                    stored_document.content,
                    stored_document.content_bytes,
                    structured_json,
                    metadata_json,
                    stored_document.mime_type,
                    stored_document.language,
                )
                if immutable_existing != immutable_candidate:
                    raise IntegrityConflictError(
                        f"evidence {evidence.evidence_id} is immutable and already differs"
                    )
                return self._evidence_from_row(existing, include_content=True), False

            self._connection.execute(
                """
                INSERT INTO evidence(
                    evidence_id, kind, source_name, observed_at, data_as_of, published_at,
                    content_sha256, evidence_json, content_text, content_blob, structured_json,
                    metadata_json, mime_type, language, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.kind.value,
                    evidence.source_name,
                    iso(evidence.observed_at),
                    iso(evidence.data_as_of) if evidence.data_as_of else None,
                    iso(evidence.published_at) if evidence.published_at else None,
                    content_hash,
                    evidence_json,
                    stored_document.content,
                    stored_document.content_bytes,
                    structured_json,
                    metadata_json,
                    stored_document.mime_type,
                    stored_document.language,
                    iso(stored_document.ingested_at),
                ),
            )
        return stored_document, True

    def get_evidence(self, evidence_id: str, *, include_content: bool = False) -> EvidenceDocument:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"evidence {evidence_id} was not found")
        return self._evidence_from_row(row, include_content=include_content)

    def list_evidence(
        self,
        *,
        knowledge_as_of: datetime,
        limit: int = 500,
    ) -> list[EvidenceDocument]:
        bounded_limit = max(1, min(limit, 5000))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM evidence
                WHERE observed_at <= ?
                ORDER BY observed_at DESC, evidence_id ASC
                LIMIT ?
                """,
                (iso(knowledge_as_of), bounded_limit),
            ).fetchall()
        return [self._evidence_from_row(row, include_content=False) for row in rows]

    @staticmethod
    def _evidence_from_row(
        row: sqlite3.Row,
        *,
        include_content: bool,
    ) -> EvidenceDocument:
        structured = json.loads(row["structured_json"]) if row["structured_json"] else None
        return EvidenceDocument(
            evidence=Evidence.model_validate_json(row["evidence_json"]),
            content=row["content_text"] if include_content else None,
            content_bytes=row["content_blob"] if include_content else None,
            structured_payload=structured,
            mime_type=row["mime_type"],
            language=row["language"],
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            metadata=json.loads(row["metadata_json"]),
        )
