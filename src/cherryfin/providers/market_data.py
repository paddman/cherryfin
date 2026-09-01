from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cherryfin.core.models import (
    ClaimValue,
    ClaimValueKind,
    Evidence,
    EvidenceKind,
    FinancialClaim,
)
from cherryfin.intelligence.models import EvidenceDocument


class MarketField(StrEnum):
    LAST_PRICE = "last_price"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    MARKET_CAP = "market_cap"
    FX_RATE = "fx_rate"
    YIELD = "yield"


class MarketObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=200)
    provider_record_id: str = Field(min_length=1, max_length=200)
    instrument_id: str = Field(min_length=1, max_length=200)
    field: MarketField
    value: Decimal
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    unit: str | None = Field(default=None, max_length=80)
    data_as_of: datetime
    observed_at: datetime
    source_uri: str | None = Field(default=None, max_length=2000)
    trust_score: float = Field(default=0.95, ge=0, le=1)
    license_tag: str = Field(min_length=1, max_length=120)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("currency must be a three-letter alphabetic code")
        return normalized


class MarketDataConnector(Protocol):
    async def get_observations(
        self,
        *,
        instrument_ids: list[str],
        fields: list[MarketField],
        as_of: datetime,
    ) -> list[MarketObservation]: ...


class StaticMarketDataConnector:
    """Deterministic adapter for licensed snapshots and repeatable evaluation fixtures."""

    def __init__(self, observations: list[MarketObservation]) -> None:
        self._observations = list(observations)

    async def get_observations(
        self,
        *,
        instrument_ids: list[str],
        fields: list[MarketField],
        as_of: datetime,
    ) -> list[MarketObservation]:
        instrument_set = set(instrument_ids)
        field_set = set(fields)
        return [
            item
            for item in self._observations
            if item.instrument_id in instrument_set
            and item.field in field_set
            and item.data_as_of <= as_of
        ]


def market_observation_to_records(
    observation: MarketObservation,
) -> tuple[EvidenceDocument, FinancialClaim]:
    structured_payload = observation.model_dump(mode="json")
    material = (
        f"{observation.provider}|{observation.provider_record_id}|"
        f"{observation.instrument_id}|{observation.field.value}|"
        f"{observation.data_as_of.isoformat()}|{observation.value}"
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    evidence_id = f"ev_market_{digest[:20]}"
    claim_id = f"clm_market_{digest[20:40]}"
    unit = observation.unit or (
        "currency" if observation.currency else "number"
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.LICENSED_MARKET_DATA,
        source_name=observation.provider,
        title=(
            f"{observation.instrument_id} {observation.field.value} "
            f"at {observation.data_as_of.isoformat()}"
        ),
        uri=observation.source_uri,
        observed_at=observation.observed_at,
        data_as_of=observation.data_as_of,
        trust_score=observation.trust_score,
        license_tag=observation.license_tag,
    )
    document = EvidenceDocument(
        evidence=evidence,
        structured_payload=structured_payload,
        mime_type="application/json",
        metadata={"provider_record_id": observation.provider_record_id},
    )
    claim = FinancialClaim(
        claim_id=claim_id,
        subject_id=observation.instrument_id,
        predicate=f"market.{observation.field.value}",
        value=ClaimValue(
            kind=ClaimValueKind.DECIMAL,
            decimal_value=observation.value,
        ),
        unit=unit,
        currency=observation.currency,
        effective_at=observation.data_as_of,
        asserted_at=observation.observed_at,
        evidence_ids=[evidence_id],
        confidence=observation.trust_score,
        methodology="licensed point-in-time market-data observation",
        metadata=observation.metadata,
    )
    return document, claim
