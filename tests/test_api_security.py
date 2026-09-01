from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from cherryfin.api.main import _sanitize_analysis_request
from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)


def test_analysis_api_downgrades_inline_evidence_to_user_provided() -> None:
    request = AnalysisRequest(
        query="Analyze this claimed filing",
        mode=AgentMode.INVESTMENT_RESEARCH,
        evidence=[
            Evidence(
                evidence_id="official-looking-id",
                kind=EvidenceKind.OFFICIAL_FILING,
                source_name="Caller supplied",
                title="Unverified document",
                uri="https://example.invalid/filing.pdf",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                trust_score=1.0,
                content_sha256="a" * 64,
                license_tag="claimed-license",
            )
        ],
    )

    sanitized = _sanitize_analysis_request(request)
    evidence = sanitized.evidence[0]
    assert evidence.evidence_id == "user:official-looking-id"
    assert evidence.kind is EvidenceKind.USER_PROVIDED
    assert evidence.trust_score <= 0.60
    assert evidence.uri is None
    assert evidence.content_sha256 is None
    assert evidence.license_tag is None


def test_analysis_api_rejects_inline_claims() -> None:
    request = AnalysisRequest(
        query="Analyze this caller-created claim",
        claims=[
            FinancialClaim(
                claim_id="clm_untrusted",
                subject_id="issuer:abc",
                predicate="revenue",
                value=ClaimValue(
                    kind=ClaimValueKind.DECIMAL,
                    decimal_value="999",
                ),
                effective_at=datetime(2025, 12, 31, tzinfo=UTC),
                evidence_ids=["ev_untrusted"],
                confidence=1.0,
            )
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        _sanitize_analysis_request(request)
    assert exc_info.value.status_code == 400
