import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_retire.models import HouseholdProjectionResult, ProjectionConfig
from bean_retire.parser import parse_ledger
from bean_retire.projection import accumulate, drawdown, project_household, project_owner, years_between

FIXTURE = Path(__file__).parent / "fixtures" / "sample.beancount"
TODAY = date(2026, 3, 20)


@pytest.fixture(scope="module")
def ledger():
    return parse_ledger(str(FIXTURE), today=TODAY)


# ─── years_between ────────────────────────────────────────────────────────────

def test_years_between_exact():
    assert years_between(date(2020, 1, 1), date(2025, 1, 1)) == 5


def test_years_between_partial_year_not_counted():
    # Birthday hasn't passed yet in the target year
    assert years_between(date(1985, 3, 15), date(2042, 3, 14)) == 56


def test_years_between_birthday_passed():
    assert years_between(date(1985, 3, 15), date(2042, 3, 15)) == 57


def test_years_between_today_to_person1_retirement():
    # Person1 born 1985-03-15, retires 2042-03-15; today 2026-03-20
    # 2042-03-15 is before 2026-03-20 day-of-year? No: (3,15) < (3,20) → subtract 1 → 15
    assert years_between(TODAY, date(2042, 3, 15)) == 15


# ─── accumulate ───────────────────────────────────────────────────────────────

def test_accumulate_zero_years():
    result = accumulate(Decimal("100000"), Decimal("10000"), 0, Decimal("0.07"))
    assert result == Decimal("100000")


def test_accumulate_one_year():
    # 100,000 × 1.07 + 10,000 = 117,000
    result = accumulate(Decimal("100000"), Decimal("10000"), 1, Decimal("0.07"))
    assert result == Decimal("117000")


def test_accumulate_two_years():
    # Year 1: 100,000 × 1.07 + 10,000 = 117,000
    # Year 2: 117,000 × 1.07 + 10,000 = 135,190
    result = accumulate(Decimal("100000"), Decimal("10000"), 2, Decimal("0.07"))
    assert result == Decimal("135190.00")


def test_accumulate_no_contributions():
    result = accumulate(Decimal("100000"), Decimal("0"), 1, Decimal("0.07"))
    assert result == Decimal("107000.00")


# ─── drawdown ─────────────────────────────────────────────────────────────────

def test_drawdown_sustainable_large_portfolio():
    balances, depletion = drawdown(
        portfolio=Decimal("3000000"),
        annual_income_need=Decimal("50000"),
        ss_annual_income=Decimal("30000"),
        ss_start_year=10,
        inflation_rate=Decimal("0.03"),
        return_rate=Decimal("0.07"),
        max_years=43,
    )
    assert depletion is None
    assert len(balances) == 43


def test_drawdown_depletes_small_portfolio():
    balances, depletion = drawdown(
        portfolio=Decimal("100000"),
        annual_income_need=Decimal("80000"),
        ss_annual_income=Decimal("0"),
        ss_start_year=100,
        inflation_rate=Decimal("0.03"),
        return_rate=Decimal("0.07"),
        max_years=43,
    )
    assert depletion is not None
    assert depletion < 5  # depletes very quickly


def test_drawdown_ss_income_delays_depletion():
    _, depletion_no_ss = drawdown(
        portfolio=Decimal("400000"),
        annual_income_need=Decimal("60000"),
        ss_annual_income=Decimal("0"),
        ss_start_year=0,
        inflation_rate=Decimal("0.03"),
        return_rate=Decimal("0.05"),
        max_years=43,
    )
    _, depletion_with_ss = drawdown(
        portfolio=Decimal("400000"),
        annual_income_need=Decimal("60000"),
        ss_annual_income=Decimal("28800"),
        ss_start_year=0,
        inflation_rate=Decimal("0.03"),
        return_rate=Decimal("0.05"),
        max_years=43,
    )

    # With SS income, portfolio lasts longer (or is sustainable when it wasn't)
    if depletion_no_ss is not None and depletion_with_ss is not None:
        assert depletion_with_ss >= depletion_no_ss
    else:
        # With SS it's sustainable but without it it depletes
        assert depletion_no_ss is not None and depletion_with_ss is None


def test_drawdown_ss_starts_at_correct_year():
    # With no inflation, pre-SS withdrawal = 60k/yr, post-SS withdrawal = 30k/yr.
    # The year-over-year balance increase should be larger in year 5+ than in years 0-4.
    balances, _ = drawdown(
        portfolio=Decimal("2000000"),
        annual_income_need=Decimal("60000"),
        ss_annual_income=Decimal("30000"),
        ss_start_year=5,
        inflation_rate=Decimal("0.00"),
        return_rate=Decimal("0.07"),
        max_years=20,
    )
    assert len(balances) == 20
    # Year-over-year delta before SS (years 0-4): smaller because full 60k withdrawal
    delta_year4 = balances[4] - balances[3]
    # Year-over-year delta after SS (years 5-6): larger because only 30k withdrawal
    delta_year6 = balances[6] - balances[5]
    assert delta_year6 > delta_year4


def test_drawdown_year_count_matches_max_years_when_sustainable():
    balances, depletion = drawdown(
        portfolio=Decimal("5000000"),
        annual_income_need=Decimal("40000"),
        ss_annual_income=Decimal("0"),
        ss_start_year=100,
        inflation_rate=Decimal("0.03"),
        return_rate=Decimal("0.07"),
        max_years=10,
    )
    assert depletion is None
    assert len(balances) == 10


# ─── project_owner (integration) ──────────────────────────────────────────────

def test_project_person1(ledger):
    config = ProjectionConfig()
    result = project_owner(
        owner=ledger["owners"]["person1"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )

    assert result.owner == "person1"
    assert result.retirement_date == date(2042, 3, 15)
    assert result.retirement_age == 57
    # 15 years of accumulation on $300k at 7% + $25k/yr → well over $1M
    assert result.portfolio_at_retirement > Decimal("1000000")
    assert result.annual_income_need > Decimal("0")
    assert result.annual_ss_income == Decimal("28800")  # 2400 × 12


def test_project_person1_pension_income(ledger):
    config = ProjectionConfig()
    result = project_owner(
        owner=ledger["owners"]["person1"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )
    assert result.annual_pension_income == Decimal("12000")  # 1000 × 12
    assert result.years_retirement_to_pension == 0  # pension starts at retirement age


def test_project_person2_no_pension(ledger):
    config = ProjectionConfig()
    result = project_owner(
        owner=ledger["owners"]["person2"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )
    assert result.annual_pension_income == Decimal("0")


def test_project_person2(ledger):
    config = ProjectionConfig()
    result = project_owner(
        owner=ledger["owners"]["person2"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )

    assert result.owner == "person2"
    assert result.retirement_date == date(2042, 6, 20)
    assert result.retirement_age == 55
    assert result.annual_ss_income == Decimal("21600")  # 1800 × 12


def test_project_spending_ratio_applied(ledger):
    config_80 = ProjectionConfig(spending_ratio=Decimal("0.80"))
    config_70 = ProjectionConfig(spending_ratio=Decimal("0.70"))

    args = (ledger["owners"]["person1"], ledger["accounts"], ledger["contributions"], ledger["spending"])
    r80 = project_owner(*args, config=config_80, today=TODAY)
    r70 = project_owner(*args, config=config_70, today=TODAY)

    assert r70.annual_income_need < r80.annual_income_need


def test_project_higher_return_rate_improves_outcome(ledger):
    config_low = ProjectionConfig(annual_return_rate=Decimal("0.04"))
    config_high = ProjectionConfig(annual_return_rate=Decimal("0.09"))

    args = (ledger["owners"]["person1"], ledger["accounts"], ledger["contributions"], ledger["spending"])
    r_low = project_owner(*args, config=config_low, today=TODAY)
    r_high = project_owner(*args, config=config_high, today=TODAY)

    assert r_high.portfolio_at_retirement > r_low.portfolio_at_retirement


def test_project_monte_carlo_runs(ledger):
    random.seed(42)
    config = ProjectionConfig(simulation_count=500)
    result = project_owner(
        owner=ledger["owners"]["person1"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
        run_monte_carlo=True,
    )
    mc = result.monte_carlo_result
    assert mc is not None
    assert 0.0 <= mc.probability_sustainable <= 1.0
    assert result.simulation_count == 500
    # Person1's fixture has a large portfolio — expect mostly sustainable outcomes
    assert mc.probability_sustainable > 0.5


def test_project_no_monte_carlo_by_default(ledger):
    config = ProjectionConfig()
    result = project_owner(
        owner=ledger["owners"]["person1"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )
    assert result.monte_carlo_result is None


# ─── project_household (integration) ──────────────────────────────────────────

@pytest.fixture(scope="module")
def household_result(ledger):
    return project_household(
        owners=list(ledger["owners"].values()),
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=ProjectionConfig(),
        today=TODAY,
    )


def test_household_result_type(household_result):
    assert isinstance(household_result, HouseholdProjectionResult)


def test_household_spending_not_doubled(household_result, ledger):
    # Household income need = spending_baseline × ratio (once), not per owner
    config = ProjectionConfig()
    expected = (ledger["spending"].annual_amount * config.spending_ratio).quantize(Decimal("0.01"))
    assert household_result.annual_income_need == expected


def test_household_first_retiree_is_person1(household_result):
    # person1 retires 2042-03-15, person2 retires 2042-06-20 → person1 is first
    assert household_result.first_retirement_date == date(2042, 3, 15)
    assert household_result.owners[0] == "person1"
    assert household_result.first_retirement_age == 57


def test_household_combined_portfolio_greater_than_either_owner(household_result, ledger):
    config = ProjectionConfig()
    r1 = project_owner(
        owner=ledger["owners"]["person1"],
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
    )
    assert household_result.combined_portfolio_at_first_retirement > r1.portfolio_at_retirement


def test_household_ss_stacks(household_result):
    assert household_result.annual_ss_income_by_owner["person1"] == Decimal("28800")
    assert household_result.annual_ss_income_by_owner["person2"] == Decimal("21600")
    assert household_result.total_annual_ss_income == Decimal("50400")


def test_household_pension_income(household_result):
    assert household_result.annual_pension_income_by_owner["person1"] == Decimal("12000")
    assert household_result.annual_pension_income_by_owner["person2"] == Decimal("0")
    assert household_result.total_annual_pension_income == Decimal("12000")


def test_household_sustainable(household_result):
    assert household_result.years_to_depletion is None
    assert household_result.depletion_age is None


def test_household_no_mc_by_default(household_result):
    assert household_result.monte_carlo_result is None
    assert household_result.simulation_count == 0


def test_household_monte_carlo_runs(ledger):
    random.seed(42)
    config = ProjectionConfig(simulation_count=500)
    result = project_household(
        owners=list(ledger["owners"].values()),
        accounts=ledger["accounts"],
        contributions=ledger["contributions"],
        spending=ledger["spending"],
        config=config,
        today=TODAY,
        run_monte_carlo=True,
    )
    mc = result.monte_carlo_result
    assert mc is not None
    assert 0.0 <= mc.probability_sustainable <= 1.0
    assert result.simulation_count == 500
