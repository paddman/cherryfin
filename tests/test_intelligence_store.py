from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from cherryfin.core.models import (
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)
from cherryfin.intelligence.models import (
    AssetClass,
    EvidenceDocument,
    IdentifierScheme,
    Instrument,
    InstrumentIdentifier,
    KnowledgeQuery,
)
from cherryfin.intelligence.store import (
    EvidenceDependencyError,
    IdentifierConflictError,
    IntegrityConflictError,
    SQLiteIntelligenceStore,
)


def _document(
    evidence_id: str,
    *,
    observed_at: datetime,
    content: str = "source content",
) -> EvidenceDocument:
    return EvidenceDocument(
        evidence=Evidence(
            evidence_id=evidence_id,
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name="Official Registry",
            title="Financial statements",
            observed_at=observed_at,
            data_as_of=observed_at,
            trust_score=0.98,
        ),
        content=content,
        mime_type="text/plain",
    )


def _claim(
    claim_id: str,
    evidence_id: str,
    *,
    effective_at: datetime,
    asserted_at: datetime,
    value: str = "100",
) -> FinancialClaim:
    return FinancialClaim(
        claim_id=claim_id,
        subject_id="instrument:test",
        predicate="revenue",
        value=ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=Decimal(value),
        ),
        unit="currency",
        currency="THB",
        effective_at=effective_at,
        asserted_at=asserted_at,
        evidence_ids=[evidence_id],
        confidence=0.95,
    )


def test_instrument_resolution_normalizes_identifiers() -> None:
    store = SQLiteIntelligenceStore()
    instrument = Instrument(
        name="Example Public Company",
        asset_class=AssetClass.EQUITY,
        currency="thb",
        exchange="set",
        identifiers=[
            InstrumentIdentifier(
                scheme=IdentifierScheme.TICKER,
                value=" abc ",
                venue=" set ",
                primary=True,
            )
        ],
    )
    stored, created = store.add_instrument(instrument)
    resolved = store.resolve_instrument(
        scheme=IdentifierScheme.TICKER,
        value="abc",
        venue="SET",
    )
    assert created is True
    assert resolved.instrument_id == stored.instrument_id
    assert resolved.currency == "THB"


def test_identifier_collision_is_rejected() -> None:
    store = SQLiteIntelligenceStore()
    identifier = InstrumentIdentifier(
        scheme=IdentifierScheme.ISIN,
        value="TH0000000001",
        primary=True,
    )
    store.add_instrument(
        Instrument(name="Issuer One", asset_class=AssetClass.EQUITY, identifiers=[identifier])
    )
    with pytest.raises(IdentifierConflictError):
        store.add_instrument(
            Instrument(name="Issuer Two", asset_class=AssetClass.EQUITY, identifiers=[identifier])
        )


def test_evidence_hash_is_computed_and_binary_is_supported() -> None:
    store = SQLiteIntelligenceStore()
    observed_at = datetime(2026, 1, 2, tzinfo=UTC)
    text_document, created = store.add_evidence(
        _document("ev_text", observed_at=observed_at, content="hello")
    )
    binary_document = EvidenceDocument(
        evidence=Evidence(
            evidence_id="ev_pdf",
            kind=EvidenceKind.OFFICIAL_FILING,
            source_name="Official Registry",
            title="Annual report PDF",
            observed_at=observed_at,
            trust_score=0.98,
        ),
        content_bytes=b"%PDF-test\x00\xff",
        mime_type="application/pdf",
    )
    stored_binary, _ = store.add_evidence(binary_document)
    fetched_binary = store.get_evidence("ev_pdf", include_content=True)
    assert created is True
    assert text_document.evidence.content_sha256 is not None
    assert stored_binary.evidence.content_sha256 is not None
    assert fetched_binary.content_bytes == b"%PDF-test\x00\xff"


def test_evidence_id_is_immutable() -> None:
    store = SQLiteIntelligenceStore()
    observed_at = datetime(2026, 1, 2, tzinfo=UTC)
    store.add_evidence(_document("ev_immutable", observed_at=observed_at, content="v1"))
    with pytest.raises(IntegrityConflictError):
        store.add_evidence(_document("ev_immutable", observed_at=observed_at, content="v2"))


def test_claim_requires_existing_non_future_evidence() -> None:
    store = SQLiteIntelligenceStore()
    effective_at = datetime(2025, 12, 31, tzinfo=UTC)
    with pytest.raises(EvidenceDependencyError, match="not stored"):
        store.add_claim(
            _claim(
                "clm_missing",
                "ev_missing",
                effective_at=effective_at,
                asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )

    store.add_evidence(
        _document(
            "ev_future",
            observed_at=datetime(2026, 1, 3, tzinfo=UTC),
        )
    )
    with pytest.raises(EvidenceDependencyError, match="precedes"):
        store.add_claim(
            _claim(
                "clm_future",
                "ev_future",
                effective_at=effective_at,
                asserted_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )


def test_point_in_time_query_blocks_future_knowledge_and_business_time() -> None:
    store = SQLiteIntelligenceStore()
    store.add_evidence(
        _document("ev_a", observed_at=datetime(2026, 1, 5, tzinfo=UTC))
    )
    store.add_claim(
        _claim(
            "clm_a",
            "ev_a",
            effective_at=datetime(2025, 12, 31, tzinfo=UTC),
            asserted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    )

    before_knowledge = store.query_claims(
        KnowledgeQuery(
            subject_id="instrument:test",
            business_as_of=datetime(2026, 1, 10, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 1, 4, tzinfo=UTC),
        )
    )
    before_business = store.query_claims(
        KnowledgeQuery(
            subject_id="instrument:test",
            business_as_of=datetime(2025, 12, 30, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 1, 10, tzinfo=UTC),
        )
    )
    visible = store.query_claims(
        KnowledgeQuery(
            subject_id="instrument:test",
            business_as_of=datetime(2026, 1, 10, tzinfo=UTC),
            knowledge_as_of=datetime(2026, 1, 10, tzinfo=UTC),
        )
    )
    assert before_knowledge == []
    assert before_business == []
    assert [claim.claim_id for claim in visible] == ["clm_a"]


def test_sqlite_store_reopens_with_same_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "intelligence.db"
    observed_at = datetime(2026, 1, 2, tzinfo=UTC)
    with SQLiteIntelligenceStore(str(path)) as store:
        store.add_evidence(_document("ev_persist", observed_at=observed_at))
        snapshot = store.snapshot_sha256()

    with SQLiteIntelligenceStore(str(path)) as reopened:
        assert reopened.get_evidence("ev_persist").evidence.evidence_id == "ev_persist"
        assert reopened.snapshot_sha256() == snapshot


def test_instrument_id_is_immutable() -> None:
    store = SQLiteIntelligenceStore()
    instrument_id = uuid4()
    first = Instrument(
        instrument_id=instrument_id,
        name="Original Name",
        asset_class=AssetClass.EQUITY,
        identifiers=[
            InstrumentIdentifier(
                scheme=IdentifierScheme.INTERNAL,
                value="issuer-1",
                primary=True,
            )
        ],
    )
    store.add_instrument(first)
    with pytest.raises(IntegrityConflictError):
        store.add_instrument(first.model_copy(update={"name": "Mutated Name"}))


def test_claim_confidence_cannot_exceed_supporting_evidence() -> None:
    store = SQLiteIntelligenceStore()
    observed_at = datetime(2026, 1, 2, tzinfo=UTC)
    document = _document("ev_low_trust", observed_at=observed_at)
    document = document.model_copy(
        update={"evidence": document.evidence.model_copy(update={"trust_score": 0.6})}
    )
    store.add_evidence(document)
    with pytest.raises(EvidenceDependencyError, match="least-trusted"):
        store.add_claim(
            _claim(
                "clm_overconfident",
                "ev_low_trust",
                effective_at=datetime(2025, 12, 31, tzinfo=UTC),
                asserted_at=observed_at,
            )
        )
