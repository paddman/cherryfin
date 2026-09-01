from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.core.models import AnalysisRequest
from cherryfin.intelligence.models import KnowledgeQuery
from cherryfin.intelligence.store import SQLiteIntelligenceStore


def hydrate_analysis_request(
    request: AnalysisRequest,
    *,
    store: SQLiteIntelligenceStore,
) -> AnalysisRequest:
    """Attach point-in-time claims and their evidence before the model is called."""

    context = request.knowledge_context
    if context is None:
        return request

    now = datetime.now(UTC)
    business_as_of = context.business_as_of or request.requested_as_of or now
    knowledge_as_of = context.knowledge_as_of or now
    predicates: list[str | None] = context.predicates or [None]

    claims_by_id = {claim.claim_id: claim for claim in request.claims}
    for predicate in predicates:
        remaining = context.max_claims - len(claims_by_id)
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
            claims_by_id.setdefault(claim.claim_id, claim)

    evidence_by_id = {item.evidence_id: item for item in request.evidence}
    for claim in claims_by_id.values():
        for evidence_id in claim.evidence_ids:
            if evidence_id in evidence_by_id:
                continue
            evidence_by_id[evidence_id] = store.get_evidence(
                evidence_id,
                include_content=False,
            ).evidence

    metadata = dict(request.metadata)
    metadata.update(
        {
            "knowledge_subject_id": context.subject_id,
            "knowledge_business_as_of": business_as_of.isoformat(),
            "knowledge_as_of": knowledge_as_of.isoformat(),
            "knowledge_claim_count": str(len(claims_by_id)),
            "knowledge_snapshot_sha256": store.snapshot_sha256(),
        }
    )
    return request.model_copy(
        update={
            "claims": list(claims_by_id.values()),
            "evidence": list(evidence_by_id.values()),
            "metadata": metadata,
        }
    )
