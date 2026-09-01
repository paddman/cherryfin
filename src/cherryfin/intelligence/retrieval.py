from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.core.models import AnalysisRequest
from cherryfin.intelligence.models import KnowledgeQuery
from cherryfin.intelligence.store import SQLiteIntelligenceStore


class UntrustedKnowledgeInputError(ValueError):
    """Raised when a caller attempts to inject verified claims into analysis."""


class KnowledgeSourceCollisionError(ValueError):
    """Raised when inline evidence collides with a ledger-owned evidence ID."""


def hydrate_analysis_request(
    request: AnalysisRequest,
    *,
    store: SQLiteIntelligenceStore,
) -> AnalysisRequest:
    """Attach ledger-owned point-in-time claims without allowing caller overrides."""

    context = request.knowledge_context
    if context is None:
        return request
    if request.claims:
        raise UntrustedKnowledgeInputError(
            "inline claims are not accepted with knowledge_context; "
            "verified claims must be loaded from the tenant ledger"
        )

    now = datetime.now(UTC)
    business_as_of = context.business_as_of or request.requested_as_of or now
    knowledge_as_of = context.knowledge_as_of or now
    predicates: list[str | None] = context.predicates or [None]

    ledger_claims = {}
    for predicate in predicates:
        remaining = context.max_claims - len(ledger_claims)
        if remaining <= 0:
            break
        query = KnowledgeQuery(
            subject_id=context.subject_id,
            predicate=predicate,
            business_as_of=business_as_of,
            knowledge_as_of=knowledge_as_of,
            include_disputed=True,
            limit=remaining,
        )
        for claim in store.query_claims(query):
            ledger_claims.setdefault(claim.claim_id, claim)

    ledger_evidence = {}
    for claim in ledger_claims.values():
        for evidence_id in claim.evidence_ids:
            if evidence_id not in ledger_evidence:
                ledger_evidence[evidence_id] = store.get_evidence(
                    evidence_id,
                    include_content=False,
                ).evidence

    inline_evidence = {item.evidence_id: item for item in request.evidence}
    collisions = sorted(set(inline_evidence) & set(ledger_evidence))
    if collisions:
        raise KnowledgeSourceCollisionError(
            "inline evidence IDs collide with ledger-owned evidence: " + ", ".join(collisions)
        )

    metadata = dict(request.metadata)
    metadata.update(
        {
            "knowledge_subject_id": context.subject_id,
            "knowledge_business_as_of": business_as_of.isoformat(),
            "knowledge_as_of": knowledge_as_of.isoformat(),
            "knowledge_claim_count": str(len(ledger_claims)),
            "knowledge_snapshot_sha256": store.snapshot_sha256(),
            "knowledge_tenant_id": store.tenant_id,
        }
    )
    payload = request.model_dump(mode="python")
    payload.update(
        {
            "claims": list(ledger_claims.values()),
            "evidence": [*ledger_evidence.values(), *inline_evidence.values()],
            "metadata": metadata,
            "requested_as_of": business_as_of,
        }
    )
    return AnalysisRequest.model_validate(payload)
