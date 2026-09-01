from datetime import UTC, datetime
from decimal import Decimal

from cherryfin.core.models import (
    AgentMode,
    AnalysisRequest,
    Evidence,
    EvidenceKind,
    KnowledgeContextRequest,
)
from cherryfin.intelligence.models import (
    EvidenceDocument,
    EvidenceIngestRequest,
    KnowledgeQuery,
    StatementCell,
    StatementRow,
    StatementTable,
    StatementType,
    UnitScale,
)
from cherryfin.intelligence.pipeline import EvidencePipeline
from cherryfin.intelligence.retrieval import hydrate_analysis_request
from cherryfin.intelligence.store import SQLiteIntelligenceStore


def _ingest_request(
    evidence_id: str,
    *,
    observed_at: datetime,
    revenue: str,
) -> EvidenceIngestRequest:
    return EvidenceIngestRequest(
        document=EvidenceDocument(
            evidence=Evidence(
                evidence_id=evidence_id,
                kind=EvidenceKind.OFFICIAL_FILING,
                source_name="Official Registry",
                title=f"Filing {evidence_id}",
                observed_at=observed_at,
                data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
                trust_score=0.98,
            ),
            content=f"revenue={revenue}",
            mime_type="text/plain",
        ),
        statement=StatementTable(
            subject_id="issuer:abc",
            statement_type=StatementType.INCOME_STATEMENT,
            currency="THB",
            scale=UnitScale.MILLIONS,
            rows=[
                StatementRow(
                    label="Revenue",
                    cells=[
                        StatementCell(
                            period_start=datetime(2025, 1, 1, tzinfo=UTC),
                            period_end=datetime(2025, 12, 31, tzinfo=UTC),
                            raw_value=revenue,
                        )
                    ],
                )
            ],
        ),
    )


def test_pipeline_ingests_statement_and_is_idempotent() -> None:
    store = SQLiteIntelligenceStore()
    pipeline = EvidencePipeline(store)
    request = _ingest_request(
        "ev_one",
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
        revenue="100",
    )
    first = pipeline.ingest(request)
    second = pipeline.ingest(request)
    assert first.evidence_created is True
    assert first.claims_created == 1
    assert second.evidence_created is False
    assert second.claims_created == 0
    assert first.claims[0].value.decimal_value == Decimal("100000000")


def test_pipeline_detects_conflicting_filings() -> None:
    store = SQLiteIntelligenceStore()
    pipeline = EvidencePipeline(store)
    pipeline.ingest(
        _ingest_request(
            "ev_one",
            observed_at=datetime(2026, 2, 1, tzinfo=UTC),
            revenue="100",
        )
    )
    second = pipeline.ingest(
        _ingest_request(
            "ev_two",
            observed_at=datetime(2026, 2, 10, tzinfo=UTC),
            revenue="130",
        )
    )
    assert len(second.contradictions) == 1
    assert second.claims[0].status.value == "disputed"


def test_query_preserves_status_as_known_at_the_time() -> None:
    store = SQLiteIntelligenceStore()
    pipeline = EvidencePipeline(store)
    first = pipeline.ingest(
        _ingest_request(
            "ev_one",
            observed_at=datetime(2026, 2, 1, tzinfo=UTC),
            revenue="100",
        )
    )
    pipeline.ingest(
        _ingest_request(
            "ev_two",
            observed_at=datetime(2026, 2, 10, tzinfo=UTC),
            revenue="130",
        )
    )

    before_conflict = pipeline.query(
        KnowledgeQuery(
            subject_id="issuer:abc",
            predicate="revenue",
            business_as_of=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 2, 5, tzinfo=UTC),
        )
    )
    after_conflict = pipeline.query(
        KnowledgeQuery(
            subject_id="issuer:abc",
            predicate="revenue",
            business_as_of=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 2, 11, tzinfo=UTC),
        )
    )
    assert len(before_conflict.claims) == 1
    assert before_conflict.claims[0].claim_id == first.claims[0].claim_id
    assert before_conflict.claims[0].status.value == "active"
    assert len(after_conflict.claims) == 2
    assert {claim.status.value for claim in after_conflict.claims} == {"disputed"}


def test_analysis_request_is_hydrated_from_point_in_time_ledger() -> None:
    store = SQLiteIntelligenceStore()
    pipeline = EvidencePipeline(store)
    result = pipeline.ingest(
        _ingest_request(
            "ev_one",
            observed_at=datetime(2026, 2, 1, tzinfo=UTC),
            revenue="100",
        )
    )
    request = AnalysisRequest(
        query="วิเคราะห์รายได้บริษัท",
        mode=AgentMode.INVESTMENT_RESEARCH,
        requested_as_of=datetime(2026, 1, 1, tzinfo=UTC),
        knowledge_context=KnowledgeContextRequest(
            subject_id="issuer:abc",
            predicates=["Revenue"],
            business_as_of=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 2, 5, tzinfo=UTC),
        ),
    )
    hydrated = hydrate_analysis_request(request, store=store)
    assert [claim.claim_id for claim in hydrated.claims] == [result.claims[0].claim_id]
    assert [evidence.evidence_id for evidence in hydrated.evidence] == ["ev_one"]
    assert hydrated.metadata["knowledge_claim_count"] == "1"
    assert len(hydrated.metadata["knowledge_snapshot_sha256"]) == 64
