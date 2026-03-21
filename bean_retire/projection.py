import random
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import (
    DetailRow,
    HouseholdProjectionResult,
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
    pension_annual_income: Decimal = Decimal("0"),
    pension_start_year: int = 0,
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
        pension_income = (
            pension_annual_income * inflation_factor if year >= pension_start_year else Decimal("0")
        )
        withdrawal = max(inflated_need - ss_income - pension_income, Decimal("0"))

        portfolio -= withdrawal
        if portfolio <= 0:
            return year_balances, year

        portfolio = portfolio * (Decimal("1") + return_rate)
        year_balances.append(portfolio)

    return year_balances, None


def _accumulation_rows(
    portfolio: Decimal,
    annual_contribution: Decimal,
    years: int,
    return_rate: Decimal,
    start_age: int,
    start_year: int,
) -> list[DetailRow]:
    """Compute per-year detail rows for the accumulation phase (today → retirement)."""
    rows: list[DetailRow] = []
    for year in range(years):
        portfolio_start = portfolio
        growth = portfolio * return_rate
        portfolio_end = portfolio + growth + annual_contribution
        rows.append(DetailRow(
            year_index=year,
            calendar_year=start_year + year,
            age=start_age + year,
            portfolio_start=portfolio_start.quantize(Decimal("0.01")),
            income_ss=Decimal("0"),
            income_pension=Decimal("0"),
            contributions=annual_contribution.quantize(Decimal("0.01")),
            withdrawal=Decimal("0"),
            investment_return=growth.quantize(Decimal("0.01")),
            portfolio_end=portfolio_end.quantize(Decimal("0.01")),
            life_events=[],
        ))
        portfolio = portfolio_end
    return rows


def _owner_detail_rows(
    portfolio: Decimal,
    annual_income_need: Decimal,
    ss_annual_income: Decimal,
    ss_start_year: int,
    pension_annual_income: Decimal,
    pension_start_year: int,
    inflation_rate: Decimal,
    return_rate: Decimal,
    max_years: int,
    retirement_age: int,
    retirement_year: int,    # calendar year of retirement
) -> list[DetailRow]:
    """Compute per-year detail rows for a single-owner projection."""
    rows: list[DetailRow] = []

    for year in range(max_years):
        inflation_factor = (Decimal("1") + inflation_rate) ** year
        inflated_need = annual_income_need * inflation_factor

        ss_active = year >= ss_start_year
        pension_active = year >= pension_start_year
        ss_income = ss_annual_income * inflation_factor if ss_active else Decimal("0")
        pension_income = pension_annual_income * inflation_factor if pension_active else Decimal("0")
        withdrawal = max(inflated_need - ss_income - pension_income, Decimal("0"))

        portfolio_start = portfolio
        portfolio -= withdrawal
        depleted = portfolio <= 0

        growth = portfolio * return_rate if not depleted else Decimal("0")
        portfolio_end = portfolio + growth if not depleted else Decimal("0")

        life_events: list[str] = []
        if year == ss_start_year and ss_annual_income > 0:
            life_events.append("Social Security started")
        if year == pension_start_year and pension_annual_income > 0:
            life_events.append("Pension started")

        rows.append(DetailRow(
            year_index=year,
            calendar_year=retirement_year + year,
            age=retirement_age + year,
            portfolio_start=portfolio_start.quantize(Decimal("0.01")),
            income_ss=ss_income.quantize(Decimal("0.01")),
            income_pension=pension_income.quantize(Decimal("0.01")),
            contributions=Decimal("0"),
            withdrawal=withdrawal.quantize(Decimal("0.01")),
            investment_return=growth.quantize(Decimal("0.01")),
            portfolio_end=portfolio_end.quantize(Decimal("0.01")),
            life_events=life_events,
        ))

        if depleted:
            break
        portfolio = portfolio_end

    return rows


def _household_detail_rows(
    combined: Decimal,
    annual_income_need: Decimal,
    ss_by_year: list[Decimal],
    contrib_by_year: list[Decimal],
    inflation_rate: Decimal,
    return_rate: Decimal,
    max_years: int,
    youngest_age_at_first: int,
    first_retirement_year: int,
    annual_ss: dict[str, Decimal],
    annual_pension: dict[str, Decimal],
    years_until_ss: dict[str, int],
    years_until_pension: dict[str, int],
    years_until_retired: dict[str, int],
    owner_names: list[str],
) -> list[DetailRow]:
    """Compute per-year detail rows for a household projection."""
    rows: list[DetailRow] = []

    for year in range(max_years):
        inflation_factor = (Decimal("1") + inflation_rate) ** year
        inflated_need = annual_income_need * inflation_factor
        inflated_ss = ss_by_year[year] * inflation_factor

        # Break combined income into SS and pension components for display
        ss_this_year = sum(
            (annual_ss[n] for n in owner_names if year >= years_until_ss[n]),
            Decimal("0"),
        ) * inflation_factor
        pension_this_year = sum(
            (annual_pension[n] for n in owner_names if year >= years_until_pension[n]),
            Decimal("0"),
        ) * inflation_factor

        withdrawal = max(inflated_need - inflated_ss, Decimal("0"))

        portfolio_start = combined
        combined -= withdrawal
        depleted = combined <= Decimal("0")

        growth = combined * return_rate if not depleted else Decimal("0")
        contrib = contrib_by_year[year] if not depleted else Decimal("0")
        portfolio_end = combined + growth + contrib if not depleted else Decimal("0")

        life_events: list[str] = []
        for name in owner_names:
            if year == years_until_ss[name]:
                life_events.append(f"{name.title()} Social Security started")
            if year == years_until_pension[name] and annual_pension[name] > 0:
                life_events.append(f"{name.title()} pension started")
            if year == years_until_retired[name] and years_until_retired[name] > 0:
                life_events.append(f"{name.title()} retired")

        rows.append(DetailRow(
            year_index=year,
            calendar_year=first_retirement_year + year,
            age=youngest_age_at_first + year,
            portfolio_start=portfolio_start.quantize(Decimal("0.01")),
            income_ss=ss_this_year.quantize(Decimal("0.01")),
            income_pension=pension_this_year.quantize(Decimal("0.01")),
            contributions=contrib.quantize(Decimal("0.01")),
            withdrawal=withdrawal.quantize(Decimal("0.01")),
            investment_return=growth.quantize(Decimal("0.01")),
            portfolio_end=portfolio_end.quantize(Decimal("0.01")),
            life_events=life_events,
        ))

        if depleted:
            break
        combined = portfolio_end

    return rows


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
    pension_annual_income: float = 0.0,
    pension_start_year: int = 0,
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
            pension_income = pension_annual_income * inflation_factor if year >= pension_start_year else 0.0
            withdrawal = max(inflated_need - ss_income - pension_income, 0.0)

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
    annual_pension_income = (
        (owner.pension_monthly_estimate * 12).quantize(Decimal("0.01"))
        if owner.pension_monthly_estimate is not None
        else Decimal("0")
    )

    # Years from retirement until SS / pension kicks in (0 if already past)
    years_retirement_to_ss = years_between(retirement_date, ss_date)
    pension_date = owner.pension_date
    years_retirement_to_pension = (
        years_between(retirement_date, pension_date) if pension_date is not None else 0
    )

    max_years_in_retirement = max(0, 100 - owner.retirement_age)

    # Initial withdrawal need (first year of retirement, accounting for any income already active)
    ss_active_year1 = annual_ss_income if years_retirement_to_ss == 0 else Decimal("0")
    pension_active_year1 = annual_pension_income if years_retirement_to_pension == 0 else Decimal("0")
    annual_portfolio_withdrawal_need = max(
        Decimal("0"),
        annual_income_need - ss_active_year1 - pension_active_year1,
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
        pension_annual_income=annual_pension_income,
        pension_start_year=years_retirement_to_pension,
    )

    depletion_age = (
        owner.retirement_age + depletion_year if depletion_year is not None else None
    )

    accum = _accumulation_rows(
        portfolio=current_portfolio,
        annual_contribution=annual_contribution,
        years=years_to_retirement,
        return_rate=config.annual_return_rate,
        start_age=years_between(owner.birth_date, today),
        start_year=today.year,
    )
    detail = _owner_detail_rows(
        portfolio=portfolio_at_retirement,
        annual_income_need=annual_income_need,
        ss_annual_income=annual_ss_income,
        ss_start_year=years_retirement_to_ss,
        pension_annual_income=annual_pension_income,
        pension_start_year=years_retirement_to_pension,
        inflation_rate=config.inflation_rate,
        return_rate=config.annual_return_rate,
        max_years=max_years_in_retirement,
        retirement_age=owner.retirement_age,
        retirement_year=retirement_date.year,
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
            pension_annual_income=float(annual_pension_income),
            pension_start_year=years_retirement_to_pension,
        )

    return ProjectionResult(
        owner=owner.name,
        retirement_date=retirement_date,
        retirement_age=owner.retirement_age,
        social_security_age=owner.social_security_age,
        years_retirement_to_ss=years_retirement_to_ss,
        years_retirement_to_pension=years_retirement_to_pension,
        portfolio_at_retirement=portfolio_at_retirement.quantize(Decimal("0.01")),
        annual_income_need=annual_income_need,
        annual_ss_income=annual_ss_income,
        annual_pension_income=annual_pension_income,
        annual_portfolio_withdrawal_need=annual_portfolio_withdrawal_need,
        years_to_depletion=depletion_year,
        depletion_age=depletion_age,
        fixed_rate_balances=year_balances,
        accumulation_rows=accum,
        detail_rows=detail,
        simulation_count=config.simulation_count if run_monte_carlo else 0,
        monte_carlo_result=mc_result,
    )


def _household_monte_carlo(
    combined_pool: float,
    annual_income_need: float,
    contribution_schedule: list[float],
    ss_schedule: list[float],
    inflation_rate: float,
    mean_return_rate: float,
    max_years: int,
    youngest_age_at_first_retirement: int,
    n_simulations: int,
    return_stddev: float,
) -> MonteCarloResult:
    """
    Monte Carlo simulation for household drawdown.

    contribution_schedule[year] — nominal annual contributions from still-working
    owners in each simulation year (pre-computed; indexed from first retirement).
    ss_schedule[year] — nominal annual SS income from all owners who have reached
    their SS age by that year (also pre-computed; inflation is applied in-loop).
    depletion_age is expressed as the youngest owner's age, since the youngest
    person determines the simulation horizon (lives longest).
    """
    depletion_ages: list[Optional[int]] = []

    for _ in range(n_simulations):
        sim_portfolio = combined_pool
        depleted_age = None

        for year in range(max_years):
            annual_return = max(-1.0, random.gauss(mean_return_rate, return_stddev))
            inflation_factor = (1 + inflation_rate) ** year
            inflated_need = annual_income_need * inflation_factor
            inflated_ss = ss_schedule[year] * inflation_factor
            withdrawal = max(inflated_need - inflated_ss, 0.0)

            sim_portfolio -= withdrawal
            if sim_portfolio <= 0:
                depleted_age = youngest_age_at_first_retirement + year
                break

            sim_portfolio *= 1 + annual_return
            sim_portfolio += contribution_schedule[year]

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


def project_household(
    owners: list[Owner],
    accounts: list[RetirementAccount],
    contributions: dict[str, Decimal],
    spending: SpendingBaseline,
    config: ProjectionConfig,
    today: Optional[date] = None,
    run_monte_carlo: bool = False,
) -> HouseholdProjectionResult:
    """
    Project retirement outcomes for a household of one or more owners.

    Unlike running per-owner projections, this function:

    - Counts household spending ONCE (not duplicated per owner)
    - Handles the overlap period when some owners have retired while others
      are still working: future contributions from working owners continue
      flowing into the combined portfolio pool, reducing drawdown pressure
    - Stacks Social Security income from all owners as each reaches their SS age

    Phases:
    1. Accumulation (today → first retirement date): each owner's portfolio
       grows independently with their own balance, contributions, and the shared
       return rate. All portfolios are merged into a single pool at the first
       owner's retirement date.
    2. Joint drawdown (first retirement → youngest owner's age 100): each year,
       inflation-adjusted household spending minus stacked SS income is withdrawn
       from the combined pool. Owners still working contribute to the pool after
       each year's growth.

    Use ``--owner NAME`` (CLI) to project a single owner independently against
    the full household spending baseline.
    """
    if today is None:
        today = date.today()

    owners_sorted = sorted(owners, key=lambda o: o.retirement_date)
    first_retiree = owners_sorted[0]
    first_retirement_date = first_retiree.retirement_date

    # Youngest owner (latest birth date) has the longest expected lifespan and
    # determines the simulation horizon.
    youngest = max(owners, key=lambda o: o.birth_date)

    def _balance(o: Owner) -> Decimal:
        return sum(
            (a.current_balance for a in accounts if a.owner == o.name),
            Decimal("0"),
        )

    def _contrib(o: Owner) -> Decimal:
        return sum(
            (contributions.get(a.account_name, Decimal("0")) for a in accounts if a.owner == o.name),
            Decimal("0"),
        )

    years_to_first = years_between(today, first_retirement_date)

    # Phase 1: each owner accumulates independently to the first retirement date.
    # Owners who retire later continue contributing via contrib_by_year after this.
    portfolios_at_first = {
        o.name: accumulate(_balance(o), _contrib(o), years_to_first, config.annual_return_rate)
        for o in owners_sorted
    }
    combined_at_first = sum(portfolios_at_first.values(), Decimal("0"))

    annual_income_need = (spending.annual_amount * config.spending_ratio).quantize(Decimal("0.01"))
    annual_ss: dict[str, Decimal] = {
        o.name: (o.social_security_monthly_estimate * 12).quantize(Decimal("0.01"))
        for o in owners_sorted
    }
    annual_pension: dict[str, Decimal] = {
        o.name: (
            (o.pension_monthly_estimate * 12).quantize(Decimal("0.01"))
            if o.pension_monthly_estimate is not None
            else Decimal("0")
        )
        for o in owners_sorted
    }

    # Year offsets from first_retirement_date for per-owner events.
    # years_between clamps to 0, so if an event precedes first_retirement_date
    # it is treated as already active from simulation year 0.
    years_until_retired = {
        o.name: years_between(first_retirement_date, o.retirement_date)
        for o in owners_sorted
    }
    years_until_ss = {
        o.name: years_between(first_retirement_date, o.social_security_date)
        for o in owners_sorted
    }

    youngest_age_at_first = years_between(youngest.birth_date, first_retirement_date)
    max_years = max(0, 100 - youngest_age_at_first)

    years_until_pension = {
        o.name: (
            years_between(first_retirement_date, o.pension_date)
            if o.pension_date is not None
            else max_years + 1  # never active
        )
        for o in owners_sorted
    }

    # Pre-compute per-year schedules to avoid repeated comprehension inside the loop.
    # contrib_by_year[y] — total annual contributions from owners still working in year y
    # ss_by_year[y]      — total nominal SS+pension income active in year y
    contrib_by_year: list[Decimal] = [
        sum(
            (_contrib(o) for o in owners_sorted if year < years_until_retired[o.name]),
            Decimal("0"),
        )
        for year in range(max_years)
    ]
    ss_by_year: list[Decimal] = [
        sum(
            (annual_ss[o.name] for o in owners_sorted if year >= years_until_ss[o.name]),
            Decimal("0"),
        )
        + sum(
            (annual_pension[o.name] for o in owners_sorted if year >= years_until_pension[o.name]),
            Decimal("0"),
        )
        for year in range(max_years)
    ]

    # Fixed-rate drawdown
    combined = combined_at_first
    year_balances: list[Decimal] = []
    depletion_year: Optional[int] = None

    for year in range(max_years):
        inflation_factor = (Decimal("1") + config.inflation_rate) ** year
        inflated_need = annual_income_need * inflation_factor
        inflated_ss = ss_by_year[year] * inflation_factor
        withdrawal = max(inflated_need - inflated_ss, Decimal("0"))

        combined -= withdrawal
        if combined <= Decimal("0"):
            depletion_year = year
            break

        combined = combined * (Decimal("1") + config.annual_return_rate)
        combined += contrib_by_year[year]
        year_balances.append(combined)

    depletion_age = (
        youngest_age_at_first + depletion_year if depletion_year is not None else None
    )

    # Accumulation phase: build one row per year per owner, merged into a combined balance.
    # For simplicity in the household view, show the combined portfolio growing each year.
    combined_accum_rows: list[DetailRow] = []
    for year in range(years_to_first):
        port_start = sum(
            (
                accumulate(_balance(o), _contrib(o), year, config.annual_return_rate)
                for o in owners_sorted
            ),
            Decimal("0"),
        )
        annual_contrib_total = sum((_contrib(o) for o in owners_sorted), Decimal("0"))
        growth = port_start * config.annual_return_rate
        port_end = port_start + growth + annual_contrib_total
        youngest_age_now = years_between(youngest.birth_date, today) + year
        combined_accum_rows.append(DetailRow(
            year_index=year,
            calendar_year=today.year + year,
            age=youngest_age_now,
            portfolio_start=port_start.quantize(Decimal("0.01")),
            income_ss=Decimal("0"),
            income_pension=Decimal("0"),
            contributions=annual_contrib_total.quantize(Decimal("0.01")),
            withdrawal=Decimal("0"),
            investment_return=growth.quantize(Decimal("0.01")),
            portfolio_end=port_end.quantize(Decimal("0.01")),
            life_events=[],
        ))

    detail = _household_detail_rows(
        combined=combined_at_first,
        annual_income_need=annual_income_need,
        ss_by_year=ss_by_year,
        contrib_by_year=contrib_by_year,
        inflation_rate=config.inflation_rate,
        return_rate=config.annual_return_rate,
        max_years=max_years,
        youngest_age_at_first=youngest_age_at_first,
        first_retirement_year=first_retirement_date.year,
        annual_ss=annual_ss,
        annual_pension=annual_pension,
        years_until_ss=years_until_ss,
        years_until_pension=years_until_pension,
        years_until_retired=years_until_retired,
        owner_names=[o.name for o in owners_sorted],
    )

    mc_result = None
    if run_monte_carlo:
        mc_result = _household_monte_carlo(
            combined_pool=float(combined_at_first),
            annual_income_need=float(annual_income_need),
            contribution_schedule=[float(c) for c in contrib_by_year],
            ss_schedule=[float(s) for s in ss_by_year],
            inflation_rate=float(config.inflation_rate),
            mean_return_rate=float(config.annual_return_rate),
            max_years=max_years,
            youngest_age_at_first_retirement=youngest_age_at_first,
            n_simulations=config.simulation_count,
            return_stddev=config.return_stddev,
        )

    return HouseholdProjectionResult(
        owners=[o.name for o in owners_sorted],
        first_retirement_date=first_retirement_date,
        first_retirement_age=first_retiree.retirement_age,
        combined_portfolio_at_first_retirement=combined_at_first.quantize(Decimal("0.01")),
        annual_income_need=annual_income_need,
        annual_ss_income_by_owner=annual_ss,
        annual_pension_income_by_owner=annual_pension,
        total_annual_ss_income=sum(annual_ss.values(), Decimal("0")),
        total_annual_pension_income=sum(annual_pension.values(), Decimal("0")),
        years_to_depletion=depletion_year,
        depletion_age=depletion_age,
        fixed_rate_balances=year_balances,
        accumulation_rows=combined_accum_rows,
        detail_rows=detail,
        simulation_count=config.simulation_count if run_monte_carlo else 0,
        monte_carlo_result=mc_result,
    )
