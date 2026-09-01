from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from cherryfin.core.models import ClaimValue, ClaimValueKind, Evidence, FinancialClaim
from cherryfin.intelligence.models import (
    StatementIssue,
    StatementIssueCode,
    StatementParseResult,
    StatementTable,
)

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NULL_TOKENS = {"", "-", "--", "\u2014", "\u2013", "n/a", "na", "n.a.", "nil", "none", "ไม่มี"}


def _normalize_label(value: str) -> str:
    normalized = value.translate(_THAI_DIGITS).casefold().strip()
    normalized = re.sub(r"[\s_/\\:;,.()\[\]{}-]+", " ", normalized)
    return " ".join(normalized.split())


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    predicate: str
    aliases: tuple[str, ...]
    default_unit: str = "currency"
    apply_scale: bool = True


_DEFAULT_METRICS = (
    MetricDefinition(
        "revenue",
        (
            "revenue",
            "revenues",
            "total revenue",
            "sales",
            "net sales",
            "รายได้",
            "รายได้รวม",
            "รายได้จากการขาย",
            "รายได้จากการขายและบริการ",
        ),
    ),
    MetricDefinition(
        "cost_of_revenue",
        (
            "cost of revenue",
            "cost of sales",
            "cost of goods sold",
            "cogs",
            "ต้นทุนขาย",
            "ต้นทุนขายและบริการ",
        ),
    ),
    MetricDefinition("gross_profit", ("gross profit", "กำไรขั้นต้น")),
    MetricDefinition(
        "operating_income",
        (
            "operating income",
            "operating profit",
            "profit from operations",
            "กำไรจากการดำเนินงาน",
            "กำไรจากกิจกรรมดำเนินงาน",
        ),
    ),
    MetricDefinition(
        "net_income",
        (
            "net income",
            "net profit",
            "profit for the period",
            "profit attributable to owners",
            "กำไรสุทธิ",
            "กำไรสำหรับงวด",
            "กำไรส่วนที่เป็นของผู้ถือหุ้น",
        ),
    ),
    MetricDefinition("ebitda", ("ebitda", "กำไรก่อนดอกเบี้ยภาษีค่าเสื่อมราคา")),
    MetricDefinition(
        "basic_eps",
        ("basic earnings per share", "basic eps", "กำไรต่อหุ้นขั้นพื้นฐาน"),
        default_unit="currency_per_share",
        apply_scale=False,
    ),
    MetricDefinition(
        "diluted_eps",
        ("diluted earnings per share", "diluted eps", "กำไรต่อหุ้นปรับลด"),
        default_unit="currency_per_share",
        apply_scale=False,
    ),
    MetricDefinition("total_assets", ("total assets", "สินทรัพย์รวม")),
    MetricDefinition(
        "total_liabilities",
        ("total liabilities", "หนี้สินรวม"),
    ),
    MetricDefinition(
        "total_equity",
        (
            "total equity",
            "shareholders equity",
            "stockholders equity",
            "ส่วนของผู้ถือหุ้นรวม",
            "ส่วนของผู้ถือหุ้น",
        ),
    ),
    MetricDefinition(
        "cash_and_cash_equivalents",
        (
            "cash and cash equivalents",
            "cash equivalents",
            "เงินสดและรายการเทียบเท่าเงินสด",
        ),
    ),
    MetricDefinition(
        "accounts_receivable",
        (
            "accounts receivable",
            "trade receivables",
            "ลูกหนี้การค้า",
            "ลูกหนี้การค้าและลูกหนี้อื่น",
        ),
    ),
    MetricDefinition(
        "accounts_payable",
        (
            "accounts payable",
            "trade payables",
            "เจ้าหนี้การค้า",
            "เจ้าหนี้การค้าและเจ้าหนี้อื่น",
        ),
    ),
    MetricDefinition(
        "total_debt",
        (
            "total debt",
            "borrowings",
            "interest bearing debt",
            "หนี้สินที่มีภาระดอกเบี้ย",
            "เงินกู้ยืมรวม",
        ),
    ),
    MetricDefinition(
        "operating_cash_flow",
        (
            "net cash from operating activities",
            "cash flow from operating activities",
            "operating cash flow",
            "กระแสเงินสดสุทธิจากกิจกรรมดำเนินงาน",
            "เงินสดสุทธิได้มาจากกิจกรรมดำเนินงาน",
        ),
    ),
    MetricDefinition(
        "investing_cash_flow",
        (
            "net cash from investing activities",
            "cash flow from investing activities",
            "กระแสเงินสดสุทธิจากกิจกรรมลงทุน",
        ),
    ),
    MetricDefinition(
        "financing_cash_flow",
        (
            "net cash from financing activities",
            "cash flow from financing activities",
            "กระแสเงินสดสุทธิจากกิจกรรมจัดหาเงิน",
        ),
    ),
    MetricDefinition(
        "capital_expenditure",
        (
            "capital expenditure",
            "capital expenditures",
            "capex",
            "purchase of property plant and equipment",
            "เงินสดจ่ายซื้อที่ดินอาคารและอุปกรณ์",
            "รายจ่ายฝ่ายทุน",
        ),
    ),
    MetricDefinition("free_cash_flow", ("free cash flow", "กระแสเงินสดอิสระ")),
    MetricDefinition(
        "shares_outstanding",
        (
            "shares outstanding",
            "weighted average shares",
            "จำนวนหุ้นสามัญ",
            "จำนวนหุ้นถัวเฉลี่ยถ่วงน้ำหนัก",
        ),
        default_unit="shares",
    ),
)


class MetricCatalog:
    def __init__(self, definitions: tuple[MetricDefinition, ...] = _DEFAULT_METRICS) -> None:
        self._by_alias: dict[str, MetricDefinition] = {}
        for definition in definitions:
            for alias in definition.aliases:
                key = _normalize_label(alias)
                existing = self._by_alias.get(key)
                if existing and existing.predicate != definition.predicate:
                    raise ValueError(f"duplicate metric alias: {alias}")
                self._by_alias[key] = definition

    def resolve(self, label: str) -> MetricDefinition | None:
        return self._by_alias.get(_normalize_label(label))


@dataclass(frozen=True, slots=True)
class ParsedNumber:
    value: Decimal | None
    unit_override: str | None = None
    error: str | None = None


def parse_financial_number(raw_value: str | int | float | None) -> ParsedNumber:
    if raw_value is None:
        return ParsedNumber(value=None)
    if isinstance(raw_value, float):
        if not math.isfinite(raw_value):
            return ParsedNumber(value=None, error="number is not finite")
        return ParsedNumber(value=Decimal(str(raw_value)))
    if isinstance(raw_value, int):
        return ParsedNumber(value=Decimal(raw_value))

    text = raw_value.translate(_THAI_DIGITS).strip()
    if text.casefold() in _NULL_TOKENS:
        return ParsedNumber(value=None)

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()

    unit_override = None
    if text.endswith("%"):
        unit_override = "percent"
        text = text[:-1].strip()

    text = text.replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"^[฿$€£¥]", "", text)
    text = text.replace(",", "")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return ParsedNumber(value=None, error=f"cannot parse numeric value: {raw_value!r}")

    try:
        value = Decimal(text)
    except InvalidOperation:
        return ParsedNumber(value=None, error=f"cannot parse numeric value: {raw_value!r}")
    if negative:
        value = -value
    return ParsedNumber(value=value, unit_override=unit_override)


class FinancialStatementParser:
    def __init__(self, catalog: MetricCatalog | None = None) -> None:
        self._catalog = catalog or MetricCatalog()

    def parse(self, *, table: StatementTable, evidence: Evidence) -> StatementParseResult:
        claims: list[FinancialClaim] = []
        issues: list[StatementIssue] = []

        for row in table.rows:
            metric = self._catalog.resolve(row.label)
            if metric is None:
                issues.append(
                    StatementIssue(
                        code=StatementIssueCode.UNKNOWN_LABEL,
                        row_label=row.label,
                        message="No canonical financial metric is mapped to this row label.",
                    )
                )
                continue

            for cell in row.cells:
                parsed = parse_financial_number(cell.raw_value)
                if parsed.error:
                    issues.append(
                        StatementIssue(
                            code=StatementIssueCode.INVALID_NUMBER,
                            row_label=row.label,
                            period_end=cell.period_end,
                            message=parsed.error,
                        )
                    )
                    continue
                if parsed.value is None:
                    issues.append(
                        StatementIssue(
                            code=StatementIssueCode.EMPTY_VALUE,
                            row_label=row.label,
                            period_end=cell.period_end,
                            message="The statement cell has no numeric value.",
                        )
                    )
                    continue

                value = parsed.value
                if metric.apply_scale and parsed.unit_override != "percent":
                    value *= table.scale.multiplier
                unit = parsed.unit_override or row.unit or metric.default_unit
                currency = table.currency if unit in {"currency", "currency_per_share"} else None
                claim_id = self._claim_id(
                    subject_id=table.subject_id,
                    predicate=metric.predicate,
                    period_end=cell.period_end.isoformat(),
                    value=format(value.normalize(), "f"),
                    evidence_id=evidence.evidence_id,
                )
                claims.append(
                    FinancialClaim(
                        claim_id=claim_id,
                        subject_id=table.subject_id,
                        predicate=metric.predicate,
                        value=ClaimValue(
                            kind=ClaimValueKind.DECIMAL,
                            decimal_value=value,
                        ),
                        unit=unit,
                        currency=currency,
                        period_start=cell.period_start,
                        period_end=cell.period_end,
                        effective_at=cell.period_end,
                        asserted_at=evidence.observed_at,
                        evidence_ids=[evidence.evidence_id],
                        confidence=min(evidence.trust_score, 0.99),
                        methodology="table-aware canonical statement-row extraction",
                        metadata={
                            "statement_type": table.statement_type.value,
                            "source_label": row.label,
                            "source_scale": table.scale.value,
                        },
                    )
                )

        return StatementParseResult(claims=claims, issues=issues)

    @staticmethod
    def _claim_id(
        *,
        subject_id: str,
        predicate: str,
        period_end: str,
        value: str,
        evidence_id: str,
    ) -> str:
        material = "|".join([subject_id, predicate, period_end, value, evidence_id])
        return "clm_" + hashlib.sha256(material.encode()).hexdigest()[:24]
