from __future__ import annotations

from cherryfin.core.models import (
    AnalysisRequest,
    Evidence,
    EvidenceKind,
    FinancialAnswer,
    FinancialClaim,
)

VERIFIED_EVIDENCE_KINDS = frozenset(
    {
        EvidenceKind.OFFICIAL_FILING,
        EvidenceKind.REGULATOR,
        EvidenceKind.EXCHANGE,
        EvidenceKind.LICENSED_MARKET_DATA,
        EvidenceKind.COMPANY_DISCLOSURE,
    }
)


def is_verified_evidence(evidence: Evidence) -> bool:
    """Return whether evidence crossed a reviewed source boundary."""

    return evidence.kind in VERIFIED_EVIDENCE_KINDS


def claim_has_verified_support(
    claim: FinancialClaim,
    evidence_by_id: dict[str, Evidence],
) -> bool:
    """A claim is verified only when every cited source exists and is verified."""

    return bool(claim.evidence_ids) and all(
        evidence_id in evidence_by_id
        and is_verified_evidence(evidence_by_id[evidence_id])
        for evidence_id in claim.evidence_ids
    )


def answer_has_verified_sources(
    request: AnalysisRequest,
    answer: FinancialAnswer,
) -> bool:
    """Check whether an answer cites at least one verified source or verified claim."""

    evidence_by_id = {item.evidence_id: item for item in request.evidence}
    if any(
        evidence_id in evidence_by_id
        and is_verified_evidence(evidence_by_id[evidence_id])
        for evidence_id in answer.evidence_ids_used
    ):
        return True

    claims_by_id = {item.claim_id: item for item in request.claims}
    return any(
        claim_id in claims_by_id
        and claim_has_verified_support(claims_by_id[claim_id], evidence_by_id)
        for claim_id in answer.claim_ids_used
    )
