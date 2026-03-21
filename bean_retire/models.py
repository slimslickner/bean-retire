from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class TaxType(Enum):
    TRADITIONAL = "traditional"
    ROTH = "roth"
    HSA = "hsa"
    SPLIT = "split"


@dataclass
class Owner:
    name: str
    birth_date: date
    retirement_age: int
    social_security_age: int
    social_security_monthly_estimate: Decimal
    pension_age: Optional[int] = None
    pension_monthly_estimate: Optional[Decimal] = None  # monthly USD; None = no pension

    def _date_at_age(self, age: int) -> date:
        """Return the date the owner turns `age`, handling Feb-29 birthdays."""
        year = self.birth_date.year + age
        month = self.birth_date.month
        day = self.birth_date.day
        # Feb 29 birthday in a non-leap target year → use Mar 1
        if month == 2 and day == 29:
            try:
                return date(year, 2, 29)
            except ValueError:
                return date(year, 3, 1)
        return date(year, month, day)

    @property
    def retirement_date(self) -> date:
        return self._date_at_age(self.retirement_age)

    @property
    def social_security_date(self) -> date:
        return self._date_at_age(self.social_security_age)

    @property
    def pension_date(self) -> Optional[date]:
        if self.pension_age is None:
            return None
        return self._date_at_age(self.pension_age)


@dataclass
class RetirementAccount:
    account_name: str
    owner: str
    tax_type: TaxType
    current_balance: Decimal
    traditional_fraction: Decimal = Decimal("1.0")
    roth_fraction: Decimal = Decimal("0.0")


@dataclass
class SpendingBaseline:
    annual_amount: Decimal
    years_averaged: int
    inflation_adjusted: bool


@dataclass
class ProjectionConfig:
    spending_ratio: Decimal = Decimal("0.80")
    annual_return_rate: Decimal = Decimal("0.07")
    inflation_rate: Decimal = Decimal("0.03")
    simulation_count: int = 1000
    return_stddev: float = 0.12


@dataclass
class MonteCarloResult:
    probability_sustainable: float
    median_depletion_age: Optional[int]
    p10_depletion_age: Optional[int]
    p90_depletion_age: Optional[int]


@dataclass
class ProjectionResult:
    owner: str
    retirement_date: date
    retirement_age: int
    portfolio_at_retirement: Decimal
    annual_income_need: Decimal
    annual_ss_income: Decimal
    annual_pension_income: Decimal              # Decimal("0") if no pension
    # Initial year portfolio withdrawal (income need minus active SS/pension in year 1)
    annual_portfolio_withdrawal_need: Decimal
    social_security_age: int
    years_retirement_to_ss: int                # years from retirement until SS starts
    years_retirement_to_pension: int           # years from retirement until pension starts; 0 if none
    years_to_depletion: Optional[int]          # years from retirement; None if sustainable
    depletion_age: Optional[int]               # retirement_age + years_to_depletion
    fixed_rate_balances: list[Decimal]         # year-by-year portfolio balances
    simulation_count: int = 0                  # n_simulations used; 0 if MC not run
    monte_carlo_result: Optional[MonteCarloResult] = None


@dataclass
class HouseholdProjectionResult:
    owners: list[str]                                   # owner names, first retiree first
    first_retirement_date: date
    first_retirement_age: int                           # retirement age of first retiree
    combined_portfolio_at_first_retirement: Decimal
    annual_income_need: Decimal                         # spending_baseline * spending_ratio
    annual_ss_income_by_owner: dict[str, Decimal]       # nominal annual SS per owner name
    annual_pension_income_by_owner: dict[str, Decimal]  # Decimal("0") for owners without pension
    total_annual_ss_income: Decimal                     # sum when all owners collecting
    total_annual_pension_income: Decimal                # sum when all pensions active
    years_to_depletion: Optional[int]                   # from first retirement; None = sustainable
    depletion_age: Optional[int]                        # youngest owner's age at depletion
    fixed_rate_balances: list[Decimal]
    simulation_count: int = 0
    monte_carlo_result: Optional[MonteCarloResult] = None
