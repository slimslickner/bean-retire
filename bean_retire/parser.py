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
        pension_age = int(meta["pension-age"]) if "pension-age" in meta else None
        pension_estimate = Decimal(meta["pension-estimate"]) if "pension-estimate" in meta else None

        owners[name] = Owner(
            name=name,
            birth_date=birth_date,
            retirement_age=retirement_age,
            social_security_age=ss_age,
            social_security_monthly_estimate=ss_estimate,
            pension_age=pension_age,
            pension_monthly_estimate=pension_estimate,
        )
    return owners


def parse_retirement_accounts(entries) -> list[RetirementAccount]:
    accounts = []
    for entry in entries:
        if not isinstance(entry, Open):
            continue
        meta = entry.meta
        if "account-owner" not in meta or "tax-account-type" not in meta:
            continue

        owner_name = meta["account-owner"]
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


def _sum_subtree_balance(
    real_account,
    price_map,
    as_of_date: Optional[date],
) -> Decimal:
    """
    Recursively sum the USD value of all positions in real_account and every
    descendant node in the realization tree.

    This handles the common Beancount pattern where a 401k or brokerage account
    is split into one sub-account per fund:

        Assets:Investment:Retirement:My401k           <- tagged with owner metadata
        Assets:Investment:Retirement:My401k:Cash      <- USD cash sweep
        Assets:Investment:Retirement:My401k:VFIAX     <- mutual fund shares
        Assets:Investment:Retirement:My401k:VTMGX     <- mutual fund shares

    Tagging only the parent account with ``owner`` / ``tax-account-type`` is
    sufficient; all descendant balances are rolled up automatically.

    Non-USD positions are converted using the price map (most recent price on or
    before as_of_date). If no price entry is available, the per-unit cost basis
    recorded in the transaction is used as a fallback. Positions with neither a
    price nor a cost basis are excluded.
    """
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

    for child in real_account.values():
        usd_total += _sum_subtree_balance(child, price_map, as_of_date)

    return usd_total


def compute_account_balances(
    entries,
    accounts: list[RetirementAccount],
    price_map=None,
    as_of_date: Optional[date] = None,
) -> list[RetirementAccount]:
    """
    Compute USD balance for each retirement account using beancount's realization.

    If the tagged account has commodity sub-accounts (e.g. one child account per
    mutual fund), their balances are included via recursive subtree aggregation —
    see _sum_subtree_balance. Non-USD positions are converted via the price map;
    cost basis is used as a fallback when no price entry is available.
    """
    real_root = realization.realize(entries)

    for account in accounts:
        real_account = realization.get(real_root, account.account_name)
        if real_account is None:
            continue
        account.current_balance = _sum_subtree_balance(real_account, price_map, as_of_date)

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


def _in_retirement_tree(account: str, account_names: set[str]) -> bool:
    """Return True if account is a tagged parent or a sub-account of one."""
    return account in account_names or any(account.startswith(n + ":") for n in account_names)


def compute_annual_contributions(
    entries,
    account_names: set[str],
    years: int = 2,
    today: Optional[date] = None,
) -> dict[str, Decimal]:
    """
    Annualized contributions per account over the trailing N-year window.

    A "contribution" is any positive posting to a tagged account or any of its
    sub-accounts that originates from outside the retirement account tree
    (i.e. from income, a checking account, or an employer match). Purely
    intra-account transactions — such as purchasing fund shares with cash
    already held in the parent account — are excluded to avoid double-counting.

    Common patterns both handled correctly:

    Pattern A — direct fund purchase from income (one transaction):
        Assets:Retirement:My401k:VFIAX  100 VFIAX {120.00 USD}
        Income:Salary:Gross            -12000.00 USD
    → $12,000 counted (Income is external to the retirement tree)

    Pattern B — deposit to parent then buy funds (two transactions):
        Tx 1: Assets:Retirement:My401k  12000 USD   (from income — external)
              Income:Salary:Gross       -12000 USD
        Tx 2: Assets:Retirement:My401k:VFIAX  100 VFIAX {120.00 USD}
              Assets:Retirement:My401k        -12000 USD   (internal only)
    → $12,000 counted once (Tx1), Tx2 skipped (all postings within tree)

    Non-USD postings are valued at their cost basis (units × cost-per-unit in
    USD). Postings with no USD cost basis are skipped — there is no reliable way
    to determine their USD value without a price directive.
    """
    if today is None:
        today = date.today()

    cutoff = _cutoff_date(today, years)
    totals: dict[str, Decimal] = {name: Decimal("0") for name in account_names}

    for entry in entries:
        if not isinstance(entry, Transaction):
            continue
        if entry.date < cutoff or entry.date >= today:
            continue

        # Skip purely intra-account transactions (fund purchases funded by cash
        # already inside the retirement account tree). These are internal
        # transfers/rebalancing and do not represent new external contributions.
        if all(_in_retirement_tree(p.account, account_names) for p in entry.postings):
            continue

        for posting in entry.postings:
            # Match posting to a tagged account (exact) or tagged ancestor (prefix)
            matched_account = None
            if posting.account in account_names:
                matched_account = posting.account
            else:
                for name in account_names:
                    if posting.account.startswith(name + ":"):
                        matched_account = name
                        break
            if matched_account is None:
                continue

            # Determine USD value of the posting
            if posting.units.currency == "USD":
                usd_amount = posting.units.number
            elif posting.cost is not None and posting.cost.currency == "USD":
                # Non-USD posting (e.g. fund shares bought at cost): use cost basis
                usd_amount = posting.units.number * posting.cost.number
            else:
                continue  # No USD value determinable; skip

            if usd_amount > 0:
                totals[matched_account] = totals[matched_account] + usd_amount

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
                f"account-owner: \"{owner_name}\" and tax-account-type metadata",
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
