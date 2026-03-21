import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_retire.models import ProjectionConfig
from bean_retire.parser import parse_ledger
from bean_retire.projection import accumulate, drawdown, project_owner, years_between

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
