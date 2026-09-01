from decimal import Decimal

import pytest

from cherryfin.tools.calculators import (
    calculate_compound_growth,
    calculate_loan,
    calculate_portfolio_risk,
)


def test_compound_growth_one_period() -> None:
    result = calculate_compound_growth(
        principal=Decimal("1000"),
        annual_rate_pct=Decimal("10"),
        years=1,
        compounds_per_year=1,
    )

    assert result.future_value == Decimal("1100.00")
    assert result.interest_earned == Decimal("100.00")
    assert result.total_contributed == Decimal("1000.00")


def test_compound_growth_with_zero_rate_and_contributions() -> None:
    result = calculate_compound_growth(
        principal=Decimal("100"),
        annual_rate_pct=Decimal("0"),
        years=1,
        compounds_per_year=12,
        periodic_contribution=Decimal("10"),
    )

    assert result.future_value == Decimal("220.00")
    assert result.interest_earned == Decimal("0.00")


def test_fixed_rate_loan() -> None:
    result = calculate_loan(
        principal=Decimal("100000"),
        annual_rate_pct=Decimal("12"),
        term_months=12,
    )

    assert result.monthly_payment == Decimal("8884.88")
    assert result.total_payment == Decimal("106618.55")
    assert result.total_interest == Decimal("6618.55")


def test_zero_interest_loan() -> None:
    result = calculate_loan(
        principal=Decimal("1200"),
        annual_rate_pct=Decimal("0"),
        term_months=12,
    )

    assert result.monthly_payment == Decimal("100.00")
    assert result.total_interest == Decimal("0.00")


def test_historical_portfolio_risk() -> None:
    result = calculate_portfolio_risk(
        periodic_returns=[0.10, -0.20, 0.05],
        periods_per_year=3,
    )

    assert result.total_return == pytest.approx(-0.076)
    assert result.max_drawdown == pytest.approx(-0.20)
    assert result.historical_var_95 == pytest.approx(0.20)
    assert result.historical_cvar_95 == pytest.approx(0.20)


def test_portfolio_risk_rejects_invalid_returns() -> None:
    with pytest.raises(ValueError, match="greater than -1"):
        calculate_portfolio_risk(periodic_returns=[0.1, -1.0])
