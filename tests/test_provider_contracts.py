from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cherryfin.intelligence.claims import ClaimLedger
from cherryfin.intelligence.models import (
    AssetClass,
    IdentifierScheme,
    Instrument,
    InstrumentIdentifier,
)
from cherryfin.intelligence.store import SQLiteIntelligenceStore
from cherryfin.providers.filings import (
    AllowlistedHTTPDocumentFetcher,
    FetchedDocument,
    FilingDescriptor,
    FilingType,
    StaticFilingConnector,
    filing_to_evidence_document,
)
from cherryfin.providers.market_data import (
    MarketField,
    MarketObservation,
    StaticMarketDataConnector,
    market_observation_to_records,
)


def _instrument() -> Instrument:
    return Instrument(
        name="Example Issuer",
        asset_class=AssetClass.EQUITY,
        identifiers=[
            InstrumentIdentifier(
                scheme=IdentifierScheme.TICKER,
                value="ABC",
                venue="SET",
                primary=True,
            )
        ],
    )


def test_document_fetcher_rejects_non_https_and_unlisted_hosts() -> None:
    fetcher = AllowlistedHTTPDocumentFetcher(allowed_hosts={"official.example"})
    assert fetcher.validate_uri("https://official.example/report.pdf").startswith("https://")
    with pytest.raises(ValueError, match="HTTPS"):
        fetcher.validate_uri("http://official.example/report.pdf")
    with pytest.raises(ValueError, match="not allowlisted"):
        fetcher.validate_uri("https://official.example.attacker.test/report.pdf")
    with pytest.raises(ValueError, match="credentials"):
        fetcher.validate_uri("https://user:pass@official.example/report.pdf")


@pytest.mark.asyncio
async def test_static_filing_connector_and_evidence_conversion() -> None:
    descriptor = FilingDescriptor(
        external_id="annual-2025",
        source_name="Official Registry",
        title="2025 annual report",
        uri="https://official.example/annual-2025.pdf",
        filing_type=FilingType.ANNUAL_REPORT,
        published_at=datetime(2026, 2, 1, tzinfo=UTC),
        data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
        license_tag="public-filing",
    )
    fetched = FetchedDocument(
        content=b"%PDF-test",
        mime_type="application/pdf",
        fetched_at=datetime(2026, 2, 2, tzinfo=UTC),
        content_sha256="3c87d37f1dbea6909f917ce437c390fb8e655a774387d9e69301c0b2283d5b63",
    )
    connector = StaticFilingConnector(
        descriptors=[descriptor],
        documents={descriptor.external_id: fetched},
    )
    discovered = await connector.discover(instrument=_instrument())
    returned = await connector.fetch(discovered[0])
    document = filing_to_evidence_document(descriptor=descriptor, fetched=returned)
    assert returned.content == b"%PDF-test"
    assert document.evidence.kind.value == "official_filing"
    assert document.content_bytes == b"%PDF-test"


@pytest.mark.asyncio
async def test_market_data_contract_produces_traceable_claim() -> None:
    observation = MarketObservation(
        provider="Licensed Feed",
        provider_record_id="record-001",
        instrument_id="instrument:abc",
        field=MarketField.CLOSE,
        value=Decimal("12.34"),
        currency="thb",
        data_as_of=datetime(2026, 2, 2, 9, 30, tzinfo=UTC),
        observed_at=datetime(2026, 2, 2, 9, 31, tzinfo=UTC),
        license_tag="licensed-internal-use",
    )
    connector = StaticMarketDataConnector([observation])
    observations = await connector.get_observations(
        instrument_ids=["instrument:abc"],
        fields=[MarketField.CLOSE],
        as_of=datetime(2026, 2, 2, 10, 0, tzinfo=UTC),
    )
    document, claim = market_observation_to_records(observations[0])

    store = SQLiteIntelligenceStore()
    store.add_evidence(document)
    result = ClaimLedger(store).record(claim)
    assert result.created is True
    assert result.claim.predicate == "market.close"
    assert result.claim.currency == "THB"
    assert result.claim.evidence_ids == [document.evidence.evidence_id]


def test_market_currency_requires_iso_like_code() -> None:
    with pytest.raises(ValueError, match="three-letter"):
        MarketObservation(
            provider="Licensed Feed",
            provider_record_id="record-bad-currency",
            instrument_id="instrument:abc",
            field=MarketField.CLOSE,
            value=Decimal("1"),
            currency="TH1",
            data_as_of=datetime(2026, 2, 2, tzinfo=UTC),
            observed_at=datetime(2026, 2, 2, tzinfo=UTC),
            license_tag="licensed-internal-use",
        )
