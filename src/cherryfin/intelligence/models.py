from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cherryfin.core.models import Evidence, FinancialClaim


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    BOND = "bond"
    FUND = "fund"
    FX = "fx"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    INDEX = "index"
    CASH = "cash"
    DERIVATIVE = "derivative"
    OTHER = "other"


class IdentifierScheme(StrEnum):
    TICKER = "ticker"
    EXCHANGE_SYMBOL = "exchange_symbol"
    ISIN = "isin"
    FIGI = "figi"
    CUSIP = "cusip"
    SEDOL = "sedol"
    LEI = "lei"
    INTERNAL = "internal"


class InstrumentIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: IdentifierScheme
    value: str = Field(min_length=1, max_length=120)
    venue: str | None = Field(default=None, max_length=40)
    primary: bool = False

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = "".join(value.strip().upper().split())
        if not normalized:
            raise ValueError("identifier value must not be blank")
        return normalized

    @field_validator("venue")
    @classmethod
    def normalize_venue(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.scheme.value, self.value, self.venue or ""


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=300)
    asset_class: AssetClass
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    identifiers: list[InstrumentIdentifier] = Field(min_length=1, max_length=30)
    active_from: datetime | None = None
    active_to: datetime | None = None
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

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @field_validator("exchange")
    @classmethod
    def normalize_exchange(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @model_validator(mode="after")
    def validate_identifiers_and_dates(self) -> Self:
        keys = [identifier.key for identifier in self.identifiers]
        if len(keys) != len(set(keys)):
            raise ValueError("instrument identifiers must be unique")
        primary_count = sum(identifier.primary for identifier in self.identifiers)
        if primary_count > 1:
            raise ValueError("at most one instrument identifier may be primary")
        if self.active_from and self.active_to and self.active_from >= self.active_to:
            raise ValueError("active_to must be after active_from")
        return self


class EvidenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")

    evidence: Evidence
    content: str | None = Field(default=None, max_length=2_000_000)
    content_bytes: bytes | None = Field(default=None, max_length=20_000_000)
    structured_payload: dict[str, Any] | None = None
    mime_type: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=30)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    record_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("record_sha256")
    @classmethod
    def validate_record_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("record_sha256 must be 64 hexadecimal characters")
        return normalized

    @model_validator(mode="after")
    def require_one_hashable_payload(self) -> Self:
        payload_count = sum(
            value is not None
            for value in (self.content, self.content_bytes, self.structured_payload)
        )
        if payload_count > 1:
            raise ValueError(
                "exactly one of content, content_bytes, or structured_payload may be supplied"
            )
        if payload_count == 0 and self.evidence.content_sha256 is None:
            raise ValueError("one payload or a precomputed evidence content_sha256 is required")
        return self


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1, max_length=200)
    predicate: str | None = Field(default=None, max_length=200)
    business_as_of: datetime
    knowledge_as_of: datetime
    include_disputed: bool = True
    include_superseded: bool = False
    include_retracted: bool = False
    limit: int = Field(default=500, ge=1, le=5000)

    @field_validator("predicate")
    @classmethod
    def normalize_predicate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"[^a-z0-9_.-]+", "_", value.strip().casefold()).strip("_")
        if not normalized:
            raise ValueError("predicate must contain an alphanumeric character")
        return normalized


class ClaimQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: KnowledgeQuery
    claims: list[FinancialClaim]
    evidence: list[Evidence]
    snapshot_sha256: str = Field(min_length=64, max_length=64)


class ContradictionSeverity(StrEnum):
    WARNING = "warning"
    MATERIAL = "material"


class ContradictionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ContradictionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contradiction_id: str = Field(min_length=1, max_length=120)
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    subject_id: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=200)
    severity: ContradictionSeverity
    reason: str = Field(min_length=1, max_length=2000)
    relative_delta: float | None = Field(default=None, ge=0)
    status: ContradictionStatus = ContradictionStatus.OPEN
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    accepted_claim_id: str | None = Field(default=None, max_length=120)
    resolution_note: str | None = Field(default=None, max_length=2000)

    @field_validator("claim_ids")
    @classmethod
    def normalize_claim_ids(cls, values: list[str]) -> list[str]:
        normalized = sorted(set(values))
        if len(normalized) != 2:
            raise ValueError("contradiction must reference exactly two distinct claims")
        return normalized


class LedgerWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: FinancialClaim
    created: bool
    contradictions: list[ContradictionRecord] = Field(default_factory=list)


class StatementType(StrEnum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    KPI = "kpi"
    OTHER = "other"


class UnitScale(StrEnum):
    ONES = "ones"
    THOUSANDS = "thousands"
    MILLIONS = "millions"
    BILLIONS = "billions"

    @property
    def multiplier(self) -> int:
        return {
            UnitScale.ONES: 1,
            UnitScale.THOUSANDS: 1_000,
            UnitScale.MILLIONS: 1_000_000,
            UnitScale.BILLIONS: 1_000_000_000,
        }[self]


class StatementCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_end: datetime
    raw_value: str | int | float | None
    period_start: datetime | None = None

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.period_start and self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        return self


class StatementRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=500)
    cells: list[StatementCell] = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=80)
    metadata: dict[str, str] = Field(default_factory=dict)


class StatementTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1, max_length=200)
    statement_type: StatementType
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    scale: UnitScale = UnitScale.ONES
    rows: list[StatementRow] = Field(min_length=1, max_length=2000)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("currency must be a three-letter alphabetic code")
        return normalized


class StatementIssueCode(StrEnum):
    UNKNOWN_LABEL = "unknown_label"
    EMPTY_VALUE = "empty_value"
    INVALID_NUMBER = "invalid_number"


class StatementIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StatementIssueCode
    row_label: str
    period_end: datetime | None = None
    message: str


class StatementParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[FinancialClaim] = Field(default_factory=list)
    issues: list[StatementIssue] = Field(default_factory=list)


class EvidenceIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: EvidenceDocument
    statement: StatementTable | None = None


class EvidenceIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingestion_id: UUID = Field(default_factory=uuid4)
    evidence: Evidence
    evidence_created: bool
    claims: list[FinancialClaim] = Field(default_factory=list)
    claims_created: int = 0
    contradictions: list[ContradictionRecord] = Field(default_factory=list)
    statement_issues: list[StatementIssue] = Field(default_factory=list)
    snapshot_sha256: str = Field(min_length=64, max_length=64)
