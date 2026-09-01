from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentMode(StrEnum):
    AUTO = "auto"
    PERSONAL_CFO = "personal_cfo"
    INVESTMENT_RESEARCH = "investment_research"
    PORTFOLIO_RISK = "portfolio_risk"
    BUSINESS_CFO = "business_cfo"
    TRADING_RESEARCH = "trading_research"


class RiskTolerance(StrEnum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SideEffectClass(StrEnum):
    READ = "read"
    CALCULATE = "calculate"
    SIMULATE = "simulate"
    WRITE = "write"
    EXECUTE = "execute"


class ActionKind(StrEnum):
    EDUCATE = "educate"
    COLLECT_MORE_DATA = "collect_more_data"
    CREATE_BUDGET = "create_budget"
    DEBT_PAYMENT_PLAN = "debt_payment_plan"
    PORTFOLIO_REBALANCE = "portfolio_rebalance"
    PLACE_ORDER = "place_order"
    TRANSFER_FUNDS = "transfer_funds"
    PAY_BILL = "pay_bill"
    EXPORT_REPORT = "export_report"


class EvidenceKind(StrEnum):
    USER_PROVIDED = "user_provided"
    OFFICIAL_FILING = "official_filing"
    REGULATOR = "regulator"
    EXCHANGE = "exchange"
    LICENSED_MARKET_DATA = "licensed_market_data"
    COMPANY_DISCLOSURE = "company_disclosure"
    NEWS = "news"
    MODEL_INFERENCE = "model_inference"


class ClaimValueKind(StrEnum):
    DECIMAL = "decimal"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"


class ClaimStatus(StrEnum):
    ACTIVE = "active"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


NumericValue = Annotated[Decimal | float | int, Field(union_mode="left_to_right")]


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isalpha() or len(value) != 3:
            raise ValueError("currency must be a three-letter alphabetic code")
        return value


class FinancialProfile(BaseModel):
    """Minimum suitability context. Secrets and account credentials never belong here."""

    model_config = ConfigDict(extra="forbid")

    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    base_currency: str = Field(default="THB", min_length=3, max_length=3)
    risk_tolerance: RiskTolerance = RiskTolerance.UNKNOWN
    investment_horizon_months: int | None = Field(default=None, ge=1, le=1200)
    emergency_fund_months: Decimal | None = Field(default=None, ge=0)
    goals: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("base_currency")
    @classmethod
    def normalize_base_currency(cls, value: str) -> str:
        return Money(amount=0, currency=value).currency


class Evidence(BaseModel):
    """A provenance envelope. Text from sources is always treated as untrusted data."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=120)
    kind: EvidenceKind
    source_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    uri: str | None = Field(default=None, max_length=2000)
    observed_at: datetime
    data_as_of: datetime | None = None
    published_at: datetime | None = None
    trust_score: float = Field(ge=0, le=1)
    excerpt: str | None = Field(default=None, max_length=2000)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    license_tag: str | None = Field(default=None, max_length=120)

    @field_validator("content_sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("content_sha256 must be 64 hexadecimal characters")
        return normalized


class ClaimValue(BaseModel):
    """Typed value that avoids ambiguous JSON unions in persisted financial claims."""

    model_config = ConfigDict(extra="forbid")

    kind: ClaimValueKind
    decimal_value: Decimal | None = None
    text_value: str | None = Field(default=None, max_length=20_000)
    boolean_value: bool | None = None
    date_value: date | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        populated = {
            ClaimValueKind.DECIMAL: self.decimal_value is not None,
            ClaimValueKind.TEXT: self.text_value is not None,
            ClaimValueKind.BOOLEAN: self.boolean_value is not None,
            ClaimValueKind.DATE: self.date_value is not None,
        }
        if sum(populated.values()) != 1 or not populated[self.kind]:
            raise ValueError("exactly one value matching kind must be populated")
        return self

    def canonical(self) -> str:
        if self.kind is ClaimValueKind.DECIMAL:
            assert self.decimal_value is not None
            return format(self.decimal_value.normalize(), "f")
        if self.kind is ClaimValueKind.TEXT:
            assert self.text_value is not None
            return " ".join(self.text_value.casefold().split())
        if self.kind is ClaimValueKind.BOOLEAN:
            return "true" if self.boolean_value else "false"
        assert self.date_value is not None
        return self.date_value.isoformat()


class FinancialClaim(BaseModel):
    """A point-in-time fact supported by one or more immutable evidence records."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=120)
    subject_id: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=200)
    value: ClaimValue
    unit: str | None = Field(default=None, max_length=80)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    period_start: datetime | None = None
    period_end: datetime | None = None
    effective_at: datetime
    expires_at: datetime | None = None
    asserted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    status: ClaimStatus = ClaimStatus.ACTIVE
    supersedes_claim_id: str | None = Field(default=None, max_length=120)
    methodology: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("predicate")
    @classmethod
    def normalize_predicate(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9_.-]+", "_", value.strip().casefold()).strip("_")
        if not normalized:
            raise ValueError("predicate must contain an alphanumeric character")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return Money(amount=0, currency=value).currency

    @field_validator("evidence_ids")
    @classmethod
    def deduplicate_evidence_ids(cls, value: list[str]) -> list[str]:
        deduplicated = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not deduplicated:
            raise ValueError("at least one evidence_id is required")
        return deduplicated

    @model_validator(mode="after")
    def validate_time_ranges(self) -> Self:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        if self.expires_at and self.effective_at >= self.expires_at:
            raise ValueError("expires_at must be after effective_at")
        return self


class KnowledgeContextRequest(BaseModel):
    """Point-in-time retrieval request used to hydrate an analysis safely."""

    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1, max_length=200)
    predicates: list[str] = Field(default_factory=list, max_length=100)
    business_as_of: datetime | None = None
    knowledge_as_of: datetime | None = None
    max_claims: int = Field(default=100, ge=1, le=500)

    @field_validator("predicates")
    @classmethod
    def normalize_predicates(cls, values: list[str]) -> list[str]:
        normalized = [FinancialClaim.normalize_predicate(value) for value in values]
        return list(dict.fromkeys(normalized))


class CalculationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    formula: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, NumericValue | str]
    result: NumericValue | str
    unit: str | None = Field(default=None, max_length=50)
    deterministic: bool = True


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    level: RiskLevel
    message: str = Field(min_length=1, max_length=1000)
    mitigation: str | None = Field(default=None, max_length=1000)


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID = Field(default_factory=uuid4)
    kind: ActionKind
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2000)
    side_effect: SideEffectClass
    notional: Money | None = None
    reversible: bool = False
    approval_required: bool = True
    idempotency_key: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=20_000)
    mode: AgentMode = AgentMode.AUTO
    profile: FinancialProfile | None = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=500)
    claims: list[FinancialClaim] = Field(default_factory=list, max_length=500)
    knowledge_context: KnowledgeContextRequest | None = None
    requested_as_of: datetime | None = None
    sensitive_data_consent: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class FinancialAnswer(BaseModel):
    """Structured answer. It deliberately contains no hidden chain-of-thought field."""

    model_config = ConfigDict(extra="forbid")

    answer_id: UUID = Field(default_factory=uuid4)
    mode: AgentMode
    summary: str = Field(min_length=1, max_length=5000)
    key_findings: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    calculations: list[CalculationTrace] = Field(default_factory=list, max_length=100)
    evidence_ids_used: list[str] = Field(default_factory=list, max_length=500)
    claim_ids_used: list[str] = Field(default_factory=list, max_length=500)
    risks: list[RiskFlag] = Field(default_factory=list, max_length=100)
    proposed_actions: list[ProposedAction] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)
    confidence_reasons: list[str] = Field(default_factory=list, max_length=20)
    as_of: datetime
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    execution_allowed: bool
    requires_human_approval: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    passed: bool
    critical_failures: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    answer: FinancialAnswer
    policy: PolicyDecision
    evaluation: EvaluationSummary
