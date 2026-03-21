from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bean_retire.models import TaxType
from bean_retire.parser import parse_ledger

FIXTURE = Path(__file__).parent / "fixtures" / "sample.beancount"
TODAY = date(2026, 3, 20)


@pytest.fixture(scope="module")
def ledger():
    return parse_ledger(str(FIXTURE), today=TODAY)


# ─── Owners ───────────────────────────────────────────────────────────────────

def test_owners_found(ledger):
    assert "person1" in ledger["owners"]
    assert "person2" in ledger["owners"]


def test_person1_owner_fields(ledger):
    person1 = ledger["owners"]["person1"]
    assert person1.birth_date == date(1985, 3, 15)
    assert person1.retirement_age == 57
    assert person1.social_security_age == 67
    assert person1.social_security_monthly_estimate == Decimal("2400")


def test_person2_owner_fields(ledger):
    person2 = ledger["owners"]["person2"]
    assert person2.birth_date == date(1987, 6, 20)
    assert person2.retirement_age == 55
    assert person2.social_security_age == 67
    assert person2.social_security_monthly_estimate == Decimal("1800")


def test_person1_retirement_date(ledger):
    assert ledger["owners"]["person1"].retirement_date == date(2042, 3, 15)


def test_person2_retirement_date(ledger):
    assert ledger["owners"]["person2"].retirement_date == date(2042, 6, 20)


# ─── Accounts ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def accounts_by_name(ledger):
    return {a.account_name: a for a in ledger["accounts"]}


def test_three_retirement_accounts(ledger):
    assert len(ledger["accounts"]) == 3


def test_person1s_401k_metadata(accounts_by_name):
    acct = accounts_by_name["Assets:Investment:Retirement:Person1s-401k"]
    assert acct.tax_type == TaxType.SPLIT
    assert acct.owner == "person1"
    assert acct.traditional_fraction == Decimal("65") / Decimal("100")
    assert acct.roth_fraction == Decimal("35") / Decimal("100")


def test_person1s_roth_ira_metadata(accounts_by_name):
    acct = accounts_by_name["Assets:Investment:Retirement:Person1s-Roth-IRA"]
    assert acct.tax_type == TaxType.ROTH
    assert acct.owner == "person1"
    assert acct.traditional_fraction == Decimal("0.0")
    assert acct.roth_fraction == Decimal("1.0")


def test_person2s_403b_metadata(accounts_by_name):
    acct = accounts_by_name["Assets:Investment:Retirement:Person2s-403b"]
    assert acct.tax_type == TaxType.TRADITIONAL
    assert acct.owner == "person2"
    assert acct.traditional_fraction == Decimal("1.0")


# ─── Balances ─────────────────────────────────────────────────────────────────
# Opening + 4 annual contributions:
# Person1s-401k:     150,000 + 4 × 18,000 = 222,000
# Person1s-Roth-IRA:  50,000 + 4 × 7,000  =  78,000
# Person2s-403b:     80,000 + 4 × 10,000 = 120,000

def test_person1s_401k_balance(accounts_by_name):
    assert accounts_by_name["Assets:Investment:Retirement:Person1s-401k"].current_balance == Decimal("222000")


def test_person1s_roth_ira_balance(accounts_by_name):
    assert accounts_by_name["Assets:Investment:Retirement:Person1s-Roth-IRA"].current_balance == Decimal("78000")


def test_person2s_403b_balance(accounts_by_name):
    assert accounts_by_name["Assets:Investment:Retirement:Person2s-403b"].current_balance == Decimal("120000")


# ─── Spending baseline ────────────────────────────────────────────────────────
# Raw: $42,000/yr for 2023, 2024, 2025
# Inflation-adjusted to 2026 at 3%:
#   2023 (3 yr ago): 42000 × 1.03³ = 45,894.534
#   2024 (2 yr ago): 42000 × 1.03² = 44,557.80
#   2025 (1 yr ago): 42000 × 1.03¹ = 43,260.00
# Average: 44,570.78

def test_spending_years_averaged(ledger):
    assert ledger["spending"].years_averaged == 3


def test_spending_inflation_adjusted(ledger):
    assert ledger["spending"].inflation_adjusted is True


def test_spending_annual_amount(ledger):
    amount = float(ledger["spending"].annual_amount)
    assert abs(amount - 44570.78) < 1.0


# ─── Contributions ────────────────────────────────────────────────────────────
# 2-year window from 2026-03-20 → cutoff 2024-03-20
# 2024-06-15 and 2025-06-15 contributions both fall in range.
# Annualized: total / 2

def test_person1s_401k_contributions(ledger):
    contrib = ledger["contributions"]["Assets:Investment:Retirement:Person1s-401k"]
    assert contrib == Decimal("18000.00")


def test_person1s_roth_contributions(ledger):
    contrib = ledger["contributions"]["Assets:Investment:Retirement:Person1s-Roth-IRA"]
    assert contrib == Decimal("7000.00")


def test_person2s_403b_contributions(ledger):
    contrib = ledger["contributions"]["Assets:Investment:Retirement:Person2s-403b"]
    assert contrib == Decimal("10000.00")
