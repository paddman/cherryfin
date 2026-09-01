from datetime import UTC, datetime
from decimal import Decimal

from cherryfin.core.models import Evidence, EvidenceKind
from cherryfin.intelligence.models import (
    StatementCell,
    StatementIssueCode,
    StatementRow,
    StatementTable,
    StatementType,
    UnitScale,
)
from cherryfin.intelligence.statements import (
    FinancialStatementParser,
    parse_financial_number,
)


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_statement",
        kind=EvidenceKind.OFFICIAL_FILING,
        source_name="Official Registry",
        title="2025 audited statements",
        observed_at=datetime(2026, 2, 15, tzinfo=UTC),
        data_as_of=datetime(2025, 12, 31, tzinfo=UTC),
        trust_score=0.98,
    )


def test_parser_maps_thai_and_english_rows_and_applies_scale() -> None:
    table = StatementTable(
        subject_id="issuer:abc",
        statement_type=StatementType.INCOME_STATEMENT,
        currency="THB",
        scale=UnitScale.MILLIONS,
        rows=[
            StatementRow(
                label="รายได้รวม",
                cells=[
                    StatementCell(
                        period_start=datetime(2025, 1, 1, tzinfo=UTC),
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="1,234.50",
                    )
                ],
            ),
            StatementRow(
                label="Net income",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="(25.25)",
                    )
                ],
            ),
        ],
    )
    result = FinancialStatementParser().parse(table=table, evidence=_evidence())
    by_predicate = {claim.predicate: claim for claim in result.claims}
    assert by_predicate["revenue"].value.decimal_value == Decimal("1234500000.00")
    assert by_predicate["net_income"].value.decimal_value == Decimal("-25250000.00")
    assert by_predicate["revenue"].currency == "THB"
    assert result.issues == []


def test_eps_is_not_multiplied_by_statement_scale() -> None:
    table = StatementTable(
        subject_id="issuer:abc",
        statement_type=StatementType.INCOME_STATEMENT,
        currency="THB",
        scale=UnitScale.MILLIONS,
        rows=[
            StatementRow(
                label="กำไรต่อหุ้นขั้นพื้นฐาน",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="2.75",
                    )
                ],
            )
        ],
    )
    claim = FinancialStatementParser().parse(table=table, evidence=_evidence()).claims[0]
    assert claim.value.decimal_value == Decimal("2.75")
    assert claim.unit == "currency_per_share"


def test_parser_reports_unknown_empty_and_invalid_cells() -> None:
    table = StatementTable(
        subject_id="issuer:abc",
        statement_type=StatementType.OTHER,
        rows=[
            StatementRow(
                label="Unknown bespoke metric",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="100",
                    )
                ],
            ),
            StatementRow(
                label="Revenue",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="—",
                    ),
                    StatementCell(
                        period_end=datetime(2024, 12, 31, tzinfo=UTC),
                        raw_value="not-a-number",
                    ),
                ],
            ),
        ],
    )
    result = FinancialStatementParser().parse(table=table, evidence=_evidence())
    assert result.claims == []
    assert {issue.code for issue in result.issues} == {
        StatementIssueCode.UNKNOWN_LABEL,
        StatementIssueCode.EMPTY_VALUE,
        StatementIssueCode.INVALID_NUMBER,
    }


def test_financial_number_parser_handles_thai_digits_and_percent() -> None:
    thai = parse_financial_number("(๑,๒๓๔.๕๐)")
    percent = parse_financial_number("12.5%")
    assert thai.value == Decimal("-1234.50")
    assert percent.value == Decimal("12.5")
    assert percent.unit_override == "percent"


def test_claim_ids_are_deterministic_for_same_cell() -> None:
    table = StatementTable(
        subject_id="issuer:abc",
        statement_type=StatementType.BALANCE_SHEET,
        currency="THB",
        rows=[
            StatementRow(
                label="สินทรัพย์รวม",
                cells=[
                    StatementCell(
                        period_end=datetime(2025, 12, 31, tzinfo=UTC),
                        raw_value="1000",
                    )
                ],
            )
        ],
    )
    parser = FinancialStatementParser()
    first = parser.parse(table=table, evidence=_evidence()).claims[0]
    second = parser.parse(table=table, evidence=_evidence()).claims[0]
    assert first.claim_id == second.claim_id
