from __future__ import annotations

import math
import statistics
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class CompoundGrowthResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal
    periodic_contribution: Decimal
    total_contributed: Decimal
    interest_earned: Decimal
    future_value: Decimal
    periods: int
    periodic_rate: Decimal


class LoanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Decimal
    monthly_payment: Decimal
    total_payment: Decimal
    total_interest: Decimal
    term_months: int
    monthly_rate: Decimal


class PortfolioRiskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: int
    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    historical_var_95: float
    historical_cvar_95: float
    sharpe_ratio: float | None
    periods_per_year: int = Field(gt=0)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_compound_growth(
    *,
    principal: Decimal,
    annual_rate_pct: Decimal,
    years: int,
    compounds_per_year: int = 12,
    periodic_contribution: Decimal = Decimal("0"),
    contribution_at_beginning: bool = False,
) -> CompoundGrowthResult:
    """Calculate future value using Decimal arithmetic for monetary values.

    The annual rate is nominal. Contributions occur once per compounding period.
    """

    if principal < 0 or periodic_contribution < 0:
        raise ValueError("principal and periodic contribution must be non-negative")
    if annual_rate_pct <= Decimal("-100"):
        raise ValueError("annual rate must be greater than -100%")
    if years < 0:
        raise ValueError("years must be non-negative")
    if compounds_per_year <= 0:
        raise ValueError("compounds_per_year must be positive")

    periods = years * compounds_per_year
    periodic_rate = annual_rate_pct / HUNDRED / Decimal(compounds_per_year)

    if periods == 0:
        future_value = principal
        contribution_value = Decimal("0")
    elif periodic_rate == 0:
        contribution_value = periodic_contribution * periods
        future_value = principal + contribution_value
    else:
        growth_factor = (Decimal("1") + periodic_rate) ** periods
        principal_value = principal * growth_factor
        contribution_value = periodic_contribution * (
            (growth_factor - Decimal("1")) / periodic_rate
        )
        if contribution_at_beginning:
            contribution_value *= Decimal("1") + periodic_rate
        future_value = principal_value + contribution_value

    total_contributed = principal + periodic_contribution * periods
    interest_earned = future_value - total_contributed

    return CompoundGrowthResult(
        principal=_money(principal),
        periodic_contribution=_money(periodic_contribution),
        total_contributed=_money(total_contributed),
        interest_earned=_money(interest_earned),
        future_value=_money(future_value),
        periods=periods,
        periodic_rate=periodic_rate,
    )


def calculate_loan(
    *,
    principal: Decimal,
    annual_rate_pct: Decimal,
    term_months: int,
) -> LoanResult:
    """Return the fixed-payment amortizing loan summary."""

    if principal <= 0:
        raise ValueError("principal must be positive")
    if annual_rate_pct < 0:
        raise ValueError("annual rate must be non-negative")
    if term_months <= 0:
        raise ValueError("term_months must be positive")

    monthly_rate = annual_rate_pct / HUNDRED / Decimal("12")
    if monthly_rate == 0:
        payment = principal / Decimal(term_months)
    else:
        factor = (Decimal("1") + monthly_rate) ** (-term_months)
        payment = principal * monthly_rate / (Decimal("1") - factor)

    total_payment = payment * term_months
    total_interest = total_payment - principal

    return LoanResult(
        principal=_money(principal),
        monthly_payment=_money(payment),
        total_payment=_money(total_payment),
        total_interest=_money(total_interest),
        term_months=term_months,
        monthly_rate=monthly_rate,
    )


def calculate_portfolio_risk(
    *,
    periodic_returns: list[float],
    periods_per_year: int = 252,
    annual_risk_free_rate: float = 0.0,
) -> PortfolioRiskResult:
    """Calculate transparent historical risk metrics from decimal returns.

    Values are returned as decimal fractions: 0.10 means 10%.
    Historical VaR/CVaR are positive loss magnitudes and are not forecasts.
    """

    if len(periodic_returns) < 2:
        raise ValueError("at least two return observations are required")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if any(not math.isfinite(value) or value <= -1 for value in periodic_returns):
        raise ValueError("returns must be finite and greater than -1")
    if annual_risk_free_rate <= -1:
        raise ValueError("annual_risk_free_rate must be greater than -1")

    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in periodic_returns:
        wealth *= 1.0 + value
        peak = max(peak, wealth)
        drawdown = wealth / peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)

    total_return = wealth - 1.0
    annualized_return = wealth ** (periods_per_year / len(periodic_returns)) - 1.0
    annualized_volatility = statistics.stdev(periodic_returns) * math.sqrt(periods_per_year)

    sorted_returns = sorted(periodic_returns)
    tail_count = max(1, math.ceil(len(sorted_returns) * 0.05))
    tail = sorted_returns[:tail_count]
    historical_var_95 = max(0.0, -tail[-1])
    historical_cvar_95 = max(0.0, -statistics.mean(tail))

    periodic_risk_free = (1.0 + annual_risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess_returns = [value - periodic_risk_free for value in periodic_returns]
    periodic_volatility = statistics.stdev(periodic_returns)
    sharpe_ratio = None
    if periodic_volatility > 0:
        sharpe_ratio = (
            statistics.mean(excess_returns) / periodic_volatility * math.sqrt(periods_per_year)
        )

    return PortfolioRiskResult(
        observations=len(periodic_returns),
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        max_drawdown=max_drawdown,
        historical_var_95=historical_var_95,
        historical_cvar_95=historical_cvar_95,
        sharpe_ratio=sharpe_ratio,
        periods_per_year=periods_per_year,
    )
