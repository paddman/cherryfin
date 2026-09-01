from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cherryfin.core.models import (
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)
from cherryfin.intelligence.claims import ClaimLedger
from cherryfin.intelligence.models import (
    EvidenceDocument,
    EvidenceIngestRequest,
    StatementCell,
    StatementRow,
    StatementTable,
    StatementType,
)
from cherryfin.intelligence.pipeline import EvidencePipeline
from cherryfin.intelligence.store import RecordNotFoundError, SQLiteIntelligenceStore


def _document() -> EvidenceDocument:
    return EvidenceDocument(
        evidence=Evidence(
            evidence_id="ev_atomic",
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name="Official Registry",
            title="Annual filing",
            observed_at=datetime(2026, 2, 1, tzinfo=UTC),
            data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
            trust_score=0.98,
        ),
        content="two rows",
        mime_type="text/plain",
    )


def _statement() -> StatementTable:
    return StatementTable(
        subject_id="issuer:atomic",
        statement_type=StatementType.INCOME_STATEMENT,
        currency="THB",
        rows=[
            StatementRow(
                label="Revenue",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="100",
                    )
                ],
            ),
            StatementRow(
                label="Net income",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="10",
                    )
                ],
            ),
        ],
    )


class FailOnSecondClaim:
    def __init__(self, store: SQLiteIntelligenceStore) -> None:
        self._ledger = ClaimLedger(store)
        self._calls = 0

    def record(self, claim: FinancialClaim):
        self._calls += 1
        if self._calls == 2:
            raise RuntimeError("synthetic parser failure")
        return self._ledger.record(claim)


def test_ingestion_rolls_back_all_records_on_mid_batch_failure() -> None:
    store = SQLiteIntelligenceStore()
    pipeline = EvidencePipeline(store, ledger=FailOnSecondClaim(store))
    request = EvidenceIngestRequest(document=_document(), statement=_statement())

    with pytest.raises(RuntimeError, match="synthetic"):
        pipeline.ingest(request)

    with pytest.raises(RecordNotFoundError):
        store.get_evidence("ev_atomic")
    assert store.record_count("claims") == 0
    assert store.record_count("audit_events") == 0


def test_evidence_requires_exactly_one_payload_representation() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        EvidenceDocument(
            evidence=_document().evidence,
            content="text",
            structured_payload={"revenue": 100},
        )


def test_structured_payload_is_redacted_without_content_scope() -> None:
    store = SQLiteIntelligenceStore()
    document = EvidenceDocument(
        evidence=_document().evidence.model_copy(update={"evidence_id": "ev_structured"}),
        structured_payload={"secret_table": [1, 2, 3]},
        mime_type="application/json",
    )
    stored, _ = store.add_evidence(document)
    redacted = store.get_evidence("ev_structured", include_content=False)
    full = store.get_evidence("ev_structured", include_content=True)

    assert stored.record_sha256 is not None
    assert redacted.structured_payload is None
    assert full.structured_payload == {"secret_table": [1, 2, 3]}


def test_secure_store_assigns_knowledge_time_and_audit_chain() -> None:
    store = SQLiteIntelligenceStore(trust_client_timestamps=False)
    old_time = datetime(2020, 1, 1, tzinfo=UTC)
    document = EvidenceDocument(
        evidence=Evidence(
            evidence_id="ev_secure",
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name="Official Registry",
            title="Backfilled filing",
            observed_at=old_time,
            trust_score=0.98,
        ),
        content="revenue=100",
        ingested_at=old_time,
    )
    stored_document, _ = store.add_evidence(document)
    claim = FinancialClaim(
        claim_id="clm_secure",
        subject_id="issuer:secure",
        predicate="revenue",
        value=ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=Decimal("100"),
        ),
        effective_at=old_time,
        asserted_at=old_time,
        evidence_ids=["ev_secure"],
        confidence=0.95,
    )
    stored_claim, _ = store.add_claim(claim)

    assert stored_document.ingested_at > old_time
    assert stored_claim.asserted_at >= stored_document.ingested_at
    verification = store.verify_audit_chain()
    assert verification.valid is True
    assert verification.events_checked == 2
    assert verification.last_event_hash is not None


def test_schema_version_is_recorded() -> None:
    store = SQLiteIntelligenceStore()
    assert store.schema_version() == 2


def test_evidence_excerpt_is_redacted_without_content_scope() -> None:
    store = SQLiteIntelligenceStore()
    document = _document().model_copy(
        update={
            "evidence": _document().evidence.model_copy(
                update={
                    "evidence_id": "ev_excerpt",
                    "excerpt": "confidential extracted sentence",
                }
            )
        }
    )
    store.add_evidence(document)

    redacted = store.get_evidence("ev_excerpt", include_content=False)
    full = store.get_evidence("ev_excerpt", include_content=True)

    assert redacted.evidence.excerpt is None
    assert full.evidence.excerpt == "confidential extracted sentence"
