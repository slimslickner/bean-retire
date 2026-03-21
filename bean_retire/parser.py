import sys
from datetime import date
from decimal import Decimal
from typing import Optional

from beancount import loader
from beancount.core import prices as beancount_prices
from beancount.core import realization
from beancount.core.data import Custom, Open, Transaction

from .models import Owner, RetirementAccount, SpendingBaseline, TaxType


def parse_owners(entries) -> dict[str, Owner]:
    owners = {}
    for entry in entries:
        if not isinstance(entry, Custom):
            continue
        if entry.type != "owner":
            continue
        if not entry.values:
            continue

        name = entry.values[0].value
        meta = entry.meta

        required = ("birth-date", "retirement-age", "social-security-age", "social-security-estimate")
        missing = [k for k in required if k not in meta]
        if missing:
            print(f"[bean-retire] owner '{name}' missing metadata: {', '.join(missing)}", file=sys.stderr)
            continue

        birth_date = date.fromisoformat(meta["birth-date"])
        retirement_age = int(meta["retirement-age"])
        ss_age = int(meta["social-security-age"])
        ss_estimate = Decimal(meta["social-security-estimate"])

        owners[name] = Owner(
            name=name,
            birth_date=birth_date,
            retirement_age=retirement_age,
            social_security_age=ss_age,
            social_security_monthly_estimate=ss_estimate,
        )
    return owners


def parse_retirement_accounts(entries) -> list[RetirementAccount]:
    accounts = []
    for entry in entries:
        if not isinstance(entry, Open):
            continue
        meta = entry.meta
        if "owner" not in meta or "tax-account-type" not in meta:
            continue

        owner_name = meta["owner"]
        tax_type_str = meta["tax-account-type"]

        try:
            tax_type = TaxType(tax_type_str)
        except ValueError:
            print(
                f"[bean-retire] account '{entry.account}' has unknown tax-account-type "
                f"'{tax_type_str}' (expected: traditional, roth, hsa, split) — skipping",
                file=sys.stderr,
            )
            continue

        traditional_fraction = Decimal("1.0")
        roth_fraction = Decimal("0.0")

        if tax_type == TaxType.SPLIT:
            trad_pct = Decimal(meta.get("traditional-percent", "100"))
            roth_pct = Decimal(meta.get("roth-percent", "0"))
            total = trad_pct + roth_pct
            if total <= 0:
                print(
                    f"[bean-retire] account '{entry.account}' SPLIT percentages sum to zero — skipping",
                    file=sys.stderr,
                )
                continue
            if abs(total - Decimal("100")) > Decimal("0.01"):
                print(
                    f"[bean-retire] account '{entry.account}' SPLIT percentages sum to "
                    f"{total} (expected 100) — normalising",
                    file=sys.stderr,
                )
            traditional_fraction = trad_pct / total
            roth_fraction = roth_pct / total
        elif tax_type == TaxType.ROTH:
            traditional_fraction = Decimal("0.0")
            roth_fraction = Decimal("1.0")
        elif tax_type == TaxType.HSA:
            traditional_fraction = Decimal("0.0")
            roth_fraction = Decimal("0.0")

        accounts.append(
            RetirementAccount(
                account_name=entry.account,
                owner=owner_name,
                tax_type=tax_type,
                current_balance=Decimal("0"),
                traditional_fraction=traditional_fraction,
                roth_fraction=roth_fraction,
            )
        )
    return accounts


def compute_account_balances(
    entries,
    accounts: list[RetirementAccount],
    price_map=None,
    as_of_date: Optional[date] = None,
) -> list[RetirementAccount]:
    """
    Compute USD balance for each retirement account using beancount's realization.
    Non-USD positions are converted via the price map; falls back to cost basis.
    """
    real_root = realization.realize(entries)

    for account in accounts:
        real_account = realization.get(real_root, account.account_name)
        if real_account is None:
            continue

        usd_total = Decimal("0")
        for pos in real_account.balance:
            if pos.units.currency == "USD":
                usd_total += pos.units.number
            elif price_map is not None:
                _, price = beancount_prices.get_price(
                    price_map, (pos.units.currency, "USD"), as_of_date
                )
                if price is not None:
                    usd_total += pos.units.number * price
                elif pos.cost is not None:
                    usd_total += pos.units.number * pos.cost.number
            elif pos.cost is not None:
                usd_total += pos.units.number * pos.cost.number

        account.current_balance = usd_total

    return accounts


def _cutoff_date(today: date, years: int) -> date:
    """Return the date exactly N years before today, safe against Feb-29."""
    try:
        return date(today.year - years, today.month, today.day)
    except ValueError:
        # today is Feb 29 and (today.year - years) is not a leap year
        return date(today.year - years, 3, 1)


def compute_spending_baseline(
    entries,
    years: int = 3,
    inflation_rate: Decimal = Decimal("0.03"),
    today: Optional[date] = None,
) -> SpendingBaseline:
    if today is None:
        today = date.today()

    cutoff = _cutoff_date(today, years)

    annual_spending: dict[int, Decimal] = {}

    for entry in entries:
        if not isinstance(entry, Transaction):
            continue
        if entry.date < cutoff or entry.date >= today:
            continue

        for posting in entry.postings:
            if not posting.account.startswith("Expenses:"):
                continue
            if posting.units.currency != "USD":
                continue
            year = entry.date.year
            annual_spending[year] = annual_spending.get(year, Decimal("0")) + posting.units.number

    if not annual_spending:
        return SpendingBaseline(annual_amount=Decimal("0"), years_averaged=0, inflation_adjusted=True)

    # Inflate each year's spending to today's dollars
    adjusted_totals = []
    for year, amount in annual_spending.items():
        years_ago = today.year - year
        inflation_factor = (Decimal("1") + inflation_rate) ** years_ago
        adjusted_totals.append(amount * inflation_factor)

    avg = sum(adjusted_totals) / Decimal(str(len(adjusted_totals)))

    return SpendingBaseline(
        annual_amount=avg.quantize(Decimal("0.01")),
        years_averaged=len(adjusted_totals),
        inflation_adjusted=True,
    )


def compute_annual_contributions(
    entries,
    account_names: set[str],
    years: int = 2,
    today: Optional[date] = None,
) -> dict[str, Decimal]:
    """Annualized contributions per account over the trailing N-year window."""
    if today is None:
        today = date.today()

    cutoff = _cutoff_date(today, years)
    totals: dict[str, Decimal] = {name: Decimal("0") for name in account_names}

    for entry in entries:
        if not isinstance(entry, Transaction):
            continue
        if entry.date < cutoff or entry.date >= today:
            continue

        for posting in entry.postings:
            if posting.account not in account_names:
                continue
            if posting.units.currency != "USD":
                continue
            if posting.units.number > 0:
                totals[posting.account] = totals[posting.account] + posting.units.number

    annualized = {}
    for account, total in totals.items():
        annualized[account] = (total / Decimal(str(years))).quantize(Decimal("0.01"))

    return annualized


def parse_ledger(
    ledger_file: str,
    spending_years: int = 3,
    contribution_years: int = 2,
    inflation_rate: Decimal = Decimal("0.03"),
    today: Optional[date] = None,
) -> dict:
    """Load and parse all retirement data from a Beancount ledger."""
    entries, errors, options = loader.load_file(ledger_file)

    if errors:
        for err in errors:
            print(f"[beancount] {err}", file=sys.stderr)

    price_map = beancount_prices.build_price_map(entries)

    owners = parse_owners(entries)
    accounts = parse_retirement_accounts(entries)
    accounts = compute_account_balances(entries, accounts, price_map, today)

    # Cross-validate: accounts must reference known owners
    for acct in accounts:
        if acct.owner not in owners:
            print(
                f"[bean-retire] account '{acct.account_name}' references unknown owner "
                f"'{acct.owner}' — check your owner directives",
                file=sys.stderr,
            )

    # Warn if any owner has no retirement accounts
    for owner_name in owners:
        if not any(a.owner == owner_name for a in accounts):
            print(
                f"[bean-retire] owner '{owner_name}' has no retirement accounts tagged with "
                f"owner: \"{owner_name}\" and tax-account-type metadata",
                file=sys.stderr,
            )

    account_names = {a.account_name for a in accounts}
    contributions = compute_annual_contributions(
        entries, account_names, contribution_years, today
    )
    spending = compute_spending_baseline(entries, spending_years, inflation_rate, today)

    if spending.years_averaged == 0:
        print(
            f"[bean-retire] no Expenses:* transactions found in the trailing {spending_years}-year "
            f"window — spending baseline is $0. Use --as-of-date to shift the window.",
            file=sys.stderr,
        )

    return {
        "entries": entries,
        "errors": errors,
        "options": options,
        "owners": owners,
        "accounts": accounts,
        "contributions": contributions,
        "spending": spending,
    }
