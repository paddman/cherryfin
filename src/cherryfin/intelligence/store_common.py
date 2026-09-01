from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from cherryfin.core.models import FinancialClaim


class IntelligenceStoreError(RuntimeError):
    """Base error for deterministic intelligence-store failures."""


class RecordNotFoundError(IntelligenceStoreError):
    pass


class IntegrityConflictError(IntelligenceStoreError):
    pass


class IdentifierConflictError(IntelligenceStoreError):
    pass


class EvidenceDependencyError(IntelligenceStoreError):
    pass


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return as_utc(value).isoformat()


def json_dumps(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def claim_fingerprint(claim: FinancialClaim) -> str:
    payload = {
        "subject_id": claim.subject_id,
        "predicate": claim.predicate,
        "value": claim.value.model_dump(mode="json"),
        "unit": claim.unit,
        "currency": claim.currency,
        "period_start": iso(claim.period_start) if claim.period_start else None,
        "period_end": iso(claim.period_end) if claim.period_end else None,
        "effective_at": iso(claim.effective_at),
        "expires_at": iso(claim.expires_at) if claim.expires_at else None,
        "evidence_ids": sorted(claim.evidence_ids),
        "supersedes_claim_id": claim.supersedes_claim_id,
        "methodology": claim.methodology,
        "metadata": claim.metadata,
    }
    return hashlib.sha256(json_dumps(payload).encode()).hexdigest()
