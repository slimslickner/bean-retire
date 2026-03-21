import random
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import (
    MonteCarloResult,
    Owner,
    ProjectionConfig,
    ProjectionResult,
    RetirementAccount,
    SpendingBaseline,
)


def years_between(d1: date, d2: date) -> int:
    """Complete years from d1 to d2 (floors partial years)."""
    years = d2.year - d1.year
    if (d2.month, d2.day) < (d1.month, d1.day):
        years -= 1
    return max(0, years)


def accumulate(
    balance: Decimal,
    annual_contribution: Decimal,
    years: int,
    return_rate: Decimal,
) -> Decimal:
    """Grow a portfolio for N years, adding contributions at year-end."""
    for _ in range(years):
        balance = balance * (Decimal("1") + return_rate) + annual_contribution
    return balance


def drawdown(
    portfolio: Decimal,
    annual_income_need: Decimal,
    ss_annual_income: Decimal,
    ss_start_year: int,
    inflation_rate: Decimal,
    return_rate: Decimal,
    max_years: int,
) -> tuple[list[Decimal], Optional[int]]:
    """
    Simulate annual portfolio drawdown.

    Returns (year_end_balances, depletion_year).
    depletion_year is the 0-based year index in which the portfolio is exhausted,
    or None if the portfolio survives all max_years.
    """
    year_balances: list[Decimal] = []

    for year in range(max_years):
        inflation_factor = (Decimal("1") + inflation_rate) ** year
        inflated_need = annual_income_need * inflation_factor
        ss_income = (
            ss_annual_income * inflation_factor if year >= ss_start_year else Decimal("0")
        )
        withdrawal = max(inflated_need - ss_income, Decimal("0"))

        portfolio -= withdrawal
        if portfolio <= 0:
            return year_balances, year

        portfolio = portfolio * (Decimal("1") + return_rate)
        year_balances.append(portfolio)

    return year_balances, None


def monte_carlo(
    portfolio: Decimal,
    annual_income_need: Decimal,
    ss_annual_income: Decimal,
    ss_start_year: int,
    inflation_rate: Decimal,
    mean_return_rate: Decimal,
    max_years: int,
    retirement_age: int,
    n_simulations: int = 1000,
    return_stddev: float = 0.12,
) -> MonteCarloResult:
    # return_stddev default kept for direct callers; project_owner passes config.return_stddev
    """
    Run Monte Carlo simulations with normally-distributed annual returns.
    Reports depletion ages (retirement_age + depletion_year).
    """
    mean_rate_float = float(mean_return_rate)
    inf_rate_float = float(inflation_rate)
    need_float = float(annual_income_need)
    ss_float = float(ss_annual_income)
    start_float = float(portfolio)

    depletion_ages: list[Optional[int]] = []

    for _ in range(n_simulations):
        sim_portfolio = start_float
        depleted_age = None

        for year in range(max_years):
            # Clamp to -100% floor so the portfolio can't go negative from returns alone
            annual_return = max(-1.0, random.gauss(mean_rate_float, return_stddev))
            inflation_factor = (1 + inf_rate_float) ** year
            inflated_need = need_float * inflation_factor
            ss_income = ss_float * inflation_factor if year >= ss_start_year else 0.0
            withdrawal = max(inflated_need - ss_income, 0.0)

            sim_portfolio -= withdrawal
            if sim_portfolio <= 0:
                depleted_age = retirement_age + year
                break

            sim_portfolio *= 1 + annual_return

        depletion_ages.append(depleted_age)

    n_sustainable = sum(1 for d in depletion_ages if d is None)
    probability_sustainable = n_sustainable / n_simulations

    depleted = sorted(d for d in depletion_ages if d is not None)

    if not depleted:
        return MonteCarloResult(
            probability_sustainable=probability_sustainable,
            median_depletion_age=None,
            p10_depletion_age=None,
            p90_depletion_age=None,
        )

    n = len(depleted)
    return MonteCarloResult(
        probability_sustainable=probability_sustainable,
        median_depletion_age=depleted[n // 2],
        p10_depletion_age=depleted[int(n * 0.10)],
        p90_depletion_age=depleted[min(int(n * 0.90), n - 1)],
    )


def project_owner(
    owner: Owner,
    accounts: list[RetirementAccount],
    contributions: dict[str, Decimal],
    spending: SpendingBaseline,
    config: ProjectionConfig,
    today: Optional[date] = None,
    run_monte_carlo: bool = False,
) -> ProjectionResult:
    if today is None:
        today = date.today()

    retirement_date = owner.retirement_date
    ss_date = owner.social_security_date

    years_to_retirement = years_between(today, retirement_date)

    # Sum this owner's accounts
    owner_accounts = [a for a in accounts if a.owner == owner.name]
    current_portfolio = sum((a.current_balance for a in owner_accounts), Decimal("0"))

    # Annualized contributions for this owner
    annual_contribution = sum(
        (contributions.get(a.account_name, Decimal("0")) for a in owner_accounts),
        Decimal("0"),
    )

    # Accumulation phase
    portfolio_at_retirement = accumulate(
        current_portfolio,
        annual_contribution,
        years_to_retirement,
        config.annual_return_rate,
    )

    annual_income_need = (spending.annual_amount * config.spending_ratio).quantize(
        Decimal("0.01")
    )
    annual_ss_income = (owner.social_security_monthly_estimate * 12).quantize(Decimal("0.01"))

    # Years from retirement until SS kicks in (could be 0 if retiring after SS age)
    years_retirement_to_ss = years_between(retirement_date, ss_date)

    max_years_in_retirement = max(0, 100 - owner.retirement_age)

    # Initial withdrawal need (first year of retirement, before SS)
    annual_portfolio_withdrawal_need = max(
        Decimal("0"),
        annual_income_need - (annual_ss_income if years_retirement_to_ss == 0 else Decimal("0")),
    )

    # Fixed-rate drawdown
    year_balances, depletion_year = drawdown(
        portfolio=portfolio_at_retirement,
        annual_income_need=annual_income_need,
        ss_annual_income=annual_ss_income,
        ss_start_year=years_retirement_to_ss,
        inflation_rate=config.inflation_rate,
        return_rate=config.annual_return_rate,
        max_years=max_years_in_retirement,
    )

    depletion_age = (
        owner.retirement_age + depletion_year if depletion_year is not None else None
    )

    mc_result = None
    if run_monte_carlo:
        mc_result = monte_carlo(
            portfolio=portfolio_at_retirement,
            annual_income_need=annual_income_need,
            ss_annual_income=annual_ss_income,
            ss_start_year=years_retirement_to_ss,
            inflation_rate=config.inflation_rate,
            mean_return_rate=config.annual_return_rate,
            max_years=max_years_in_retirement,
            retirement_age=owner.retirement_age,
            n_simulations=config.simulation_count,
            return_stddev=config.return_stddev,
        )

    return ProjectionResult(
        owner=owner.name,
        retirement_date=retirement_date,
        retirement_age=owner.retirement_age,
        social_security_age=owner.social_security_age,
        years_retirement_to_ss=years_retirement_to_ss,
        portfolio_at_retirement=portfolio_at_retirement.quantize(Decimal("0.01")),
        annual_income_need=annual_income_need,
        annual_ss_income=annual_ss_income,
        annual_portfolio_withdrawal_need=annual_portfolio_withdrawal_need,
        years_to_depletion=depletion_year,
        depletion_age=depletion_age,
        fixed_rate_balances=year_balances,
        simulation_count=config.simulation_count if run_monte_carlo else 0,
        monte_carlo_result=mc_result,
    )
