from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    evidence: list[Evidence] = Field(default_factory=list, max_length=200)
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
    evidence_ids_used: list[str] = Field(default_factory=list, max_length=200)
    risks: list[RiskFlag] = Field(default_factory=list, max_length=100)
    proposed_actions: list[ProposedAction] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    confidence: float = Field(ge=0, le=1)
    confidence_reasons: list[str] = Field(default_factory=list, max_length=20)
    as_of: datetime
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
