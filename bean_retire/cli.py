import json as json_module
import sys
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import DetailRow, HouseholdProjectionResult, Owner, ProjectionConfig, ProjectionResult
from .parser import parse_ledger
from .projection import project_household, project_owner

console = Console()


def fmt_dollars(amount: Decimal) -> str:
    return f"${amount:,.0f}"


def fmt_pct(rate: float) -> str:
    return f"{rate:.1%}"


def _real_value(nominal: Decimal, calendar_year: int, base_year: int, inflation_rate: Decimal) -> Decimal:
    years = calendar_year - base_year
    if years <= 0:
        return nominal
    return nominal / (Decimal("1") + inflation_rate) ** years


def render_detail_table(
    rows: list[DetailRow],
    title: str,
    inflation_rate: Decimal = Decimal("0"),
    base_year: int = 0,
    show_taxes: bool = False,
) -> Table:
    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("Year", justify="right", style="dim")
    table.add_column("Age", justify="right")
    table.add_column("Portfolio Start", justify="right")
    table.add_column("Income (SS+Pension)", justify="right", style="green")
    table.add_column("Contributions", justify="right", style="cyan")
    table.add_column("Withdrawal", justify="right", style="red")
    if show_taxes:
        table.add_column("Taxes", justify="right", style="magenta")
    table.add_column("Return", justify="right")
    table.add_column("Portfolio End", justify="right", style="bold")
    table.add_column("Today $", justify="right", style="dim")
    table.add_column("Events", style="yellow")

    for row in rows:
        income = row.income_ss + row.income_pension
        events = ", ".join(row.life_events)
        if row.portfolio_end:
            real = _real_value(row.portfolio_end, row.calendar_year, base_year, inflation_rate)
            real_str = fmt_dollars(real)
        else:
            real_str = "[red]depleted[/red]"
        cells = [
            str(row.calendar_year),
            str(row.age),
            fmt_dollars(row.portfolio_start),
            fmt_dollars(income) if income else "—",
            fmt_dollars(row.contributions) if row.contributions else "—",
            fmt_dollars(row.withdrawal),
        ]
        if show_taxes:
            cells.append(fmt_dollars(row.taxes) if row.taxes else "—")
        cells += [
            fmt_dollars(row.investment_return),
            fmt_dollars(row.portfolio_end) if row.portfolio_end else "[red]depleted[/red]",
            real_str,
            events,
        ]
        table.add_row(*cells)

    return table


def detail_rows_to_list(
    rows: list[DetailRow],
    inflation_rate: Decimal = Decimal("0"),
    base_year: int = 0,
) -> list[dict]:
    return [
        {
            "year_index": r.year_index,
            "calendar_year": r.calendar_year,
            "age": r.age,
            "portfolio_start": float(r.portfolio_start),
            "income_ss": float(r.income_ss),
            "income_pension": float(r.income_pension),
            "contributions": float(r.contributions),
            "withdrawal": float(r.withdrawal),
            "taxes": float(r.taxes),
            "investment_return": float(r.investment_return),
            "portfolio_end": float(r.portfolio_end),
            "portfolio_end_real": float(_real_value(r.portfolio_end, r.calendar_year, base_year, inflation_rate)),
            "life_events": r.life_events,
        }
        for r in rows
    ]


def render_result(result: ProjectionResult, life_expectancy: int = 100, tax_rate: Decimal = Decimal("0")) -> Panel:
    lines = []

    sustainable = result.years_to_depletion is None
    if sustainable:
        outcome_icon = f"[green]✓ Sustainable to age {life_expectancy}[/green]"
    else:
        outcome_icon = f"[red]✗ Depleted at age {result.depletion_age}[/red]"

    ss_start_age = result.retirement_age + result.years_retirement_to_ss
    retirement_str = result.retirement_date.strftime("%B %-d, %Y")

    lines.append(f"[bold]Retirement:[/bold]  {retirement_str} (age {result.retirement_age})")
    lines.append(f"[bold]Portfolio now → at retirement:[/bold]  {fmt_dollars(result.portfolio_at_retirement)}")
    lines.append("")
    lines.append(f"[bold]Annual spending need:[/bold]  {fmt_dollars(result.annual_income_need)}")
    lines.append(
        f"[bold]Social Security:[/bold]       {fmt_dollars(result.annual_ss_income)}/yr"
        f"  (starting age {ss_start_age})"
    )
    if result.annual_pension_income > Decimal("0"):
        pension_start_age = result.retirement_age + result.years_retirement_to_pension
        lines.append(
            f"[bold]Pension:[/bold]               {fmt_dollars(result.annual_pension_income)}/yr"
            f"  (starting age {pension_start_age})"
        )
    withdrawal = fmt_dollars(result.annual_portfolio_withdrawal_need)
    lines.append(f"[bold]Portfolio withdrawal:[/bold]  {withdrawal}/yr (year 1, gross)")
    if tax_rate > Decimal("0"):
        lines.append(
            f"[bold]Tax rate:[/bold]              {fmt_pct(float(tax_rate))}"
            f"  ({fmt_pct(float(result.traditional_fraction))} of portfolio taxable)"
        )
    lines.append("")
    lines.append(f"[bold]Outcome:[/bold]  {outcome_icon}")

    if result.monte_carlo_result is not None:
        mc = result.monte_carlo_result
        lines.append("")
        lines.append(f"[bold]Monte Carlo ({result.simulation_count:,} simulations):[/bold]")
        lines.append(f"  Probability sustainable to {life_expectancy}:  {fmt_pct(mc.probability_sustainable)}")
        if mc.median_depletion_age is not None:
            lines.append(f"  Median depletion age:           {mc.median_depletion_age}")
        if mc.p10_depletion_age is not None:
            lines.append(f"  10th / 90th percentile age:     {mc.p10_depletion_age} / {mc.p90_depletion_age}")

    return Panel(
        "\n".join(lines),
        title=f"[bold cyan]{result.owner.title()}[/bold cyan]",
        expand=False,
    )


def result_to_dict(
    result: ProjectionResult,
    inflation_rate: Decimal = Decimal("0"),
    base_year: int = 0,
) -> dict[str, object]:
    d: dict[str, object] = {
        "owner": result.owner,
        "retirement_date": result.retirement_date.isoformat(),
        "retirement_age": result.retirement_age,
        "social_security_age": result.social_security_age,
        "years_retirement_to_ss": result.years_retirement_to_ss,
        "portfolio_at_retirement": float(result.portfolio_at_retirement),
        "annual_income_need": float(result.annual_income_need),
        "annual_ss_income": float(result.annual_ss_income),
        "annual_pension_income": float(result.annual_pension_income),
        "years_retirement_to_pension": result.years_retirement_to_pension,
        "annual_portfolio_withdrawal_need": float(result.annual_portfolio_withdrawal_need),
        "traditional_fraction": float(result.traditional_fraction),
        "years_to_depletion": result.years_to_depletion,
        "depletion_age": result.depletion_age,
        "sustainable": result.years_to_depletion is None,
        "accumulation": detail_rows_to_list(result.accumulation_rows, inflation_rate, base_year),
        "detail": detail_rows_to_list(result.detail_rows, inflation_rate, base_year),
        "monte_carlo": None,
    }
    if result.monte_carlo_result is not None:
        mc = result.monte_carlo_result
        d["monte_carlo"] = {
            "probability_sustainable": mc.probability_sustainable,
            "median_depletion_age": mc.median_depletion_age,
            "p10_depletion_age": mc.p10_depletion_age,
            "p90_depletion_age": mc.p90_depletion_age,
        }
    return d


def render_household_result(
    result: HouseholdProjectionResult,
    owners: dict[str, Owner],
    tax_rate: Decimal = Decimal("0"),
) -> Panel:
    lines = []

    if result.years_to_depletion is None:
        outcome_icon = "[green]✓ Sustainable[/green]"
    else:
        outcome_icon = f"[red]✗ Depleted at youngest owner age {result.depletion_age}[/red]"

    lines.append("[bold]Retirement timeline:[/bold]")
    for name in result.owners:
        o = owners[name]
        retirement_str = o.retirement_date.strftime("%B %-d, %Y")
        lines.append(f"  {name.title()}:  {retirement_str} (age {o.retirement_age})")
    lines.append("")
    lines.append(
        f"[bold]Combined portfolio at first retirement:[/bold]  "
        f"{fmt_dollars(result.combined_portfolio_at_first_retirement)}"
    )
    lines.append("")
    lines.append(f"[bold]Annual household spending need:[/bold]  {fmt_dollars(result.annual_income_need)}")
    lines.append("[bold]Social Security:[/bold]")
    for name in result.owners:
        o = owners[name]
        ss_amount = result.annual_ss_income_by_owner[name]
        lines.append(f"  {name.title()}:  {fmt_dollars(ss_amount)}/yr  (starting age {o.social_security_age})")
    if result.total_annual_pension_income > Decimal("0"):
        lines.append("[bold]Pension:[/bold]")
        for name in result.owners:
            o = owners[name]
            pension_amount = result.annual_pension_income_by_owner[name]
            if pension_amount > Decimal("0") and o.pension_age is not None:
                lines.append(f"  {name.title()}:  {fmt_dollars(pension_amount)}/yr  (starting age {o.pension_age})")
    if tax_rate > Decimal("0"):
        lines.append(
            f"[bold]Tax rate:[/bold]  {fmt_pct(float(tax_rate))}"
            f"  ({fmt_pct(float(result.traditional_fraction))} of portfolio taxable)"
        )
    lines.append("")
    lines.append(f"[bold]Outcome:[/bold]  {outcome_icon}")

    if result.monte_carlo_result is not None:
        mc = result.monte_carlo_result
        lines.append("")
        lines.append(f"[bold]Monte Carlo ({result.simulation_count:,} simulations):[/bold]")
        lines.append(f"  Probability sustainable:        {fmt_pct(mc.probability_sustainable)}")
        if mc.median_depletion_age is not None:
            lines.append(f"  Median depletion age:           {mc.median_depletion_age}")
        if mc.p10_depletion_age is not None:
            lines.append(f"  10th / 90th percentile age:     {mc.p10_depletion_age} / {mc.p90_depletion_age}")

    return Panel(
        "\n".join(lines),
        title="[bold cyan]Household[/bold cyan]",
        expand=False,
    )


def household_result_to_dict(result: HouseholdProjectionResult, owners: dict[str, Owner]) -> dict[str, object]:
    d: dict[str, object] = {
        "owners": result.owners,
        "first_retirement_date": result.first_retirement_date.isoformat(),
        "first_retirement_age": result.first_retirement_age,
        "combined_portfolio_at_first_retirement": float(result.combined_portfolio_at_first_retirement),
        "annual_income_need": float(result.annual_income_need),
        "ss_income_by_owner": {
            name: {
                "annual_amount": float(result.annual_ss_income_by_owner[name]),
                "social_security_age": owners[name].social_security_age,
            }
            for name in result.owners
        },
        "total_annual_ss_income": float(result.total_annual_ss_income),
        "pension_income_by_owner": {
            name: {
                "annual_amount": float(result.annual_pension_income_by_owner[name]),
                "pension_age": owners[name].pension_age,
            }
            for name in result.owners
        },
        "total_annual_pension_income": float(result.total_annual_pension_income),
        "traditional_fraction": float(result.traditional_fraction),
        "years_to_depletion": result.years_to_depletion,
        "depletion_age": result.depletion_age,
        "sustainable": result.years_to_depletion is None,
        "detail": detail_rows_to_list(result.detail_rows),
        "monte_carlo": None,
    }
    if result.monte_carlo_result is not None:
        mc = result.monte_carlo_result
        d["monte_carlo"] = {
            "probability_sustainable": mc.probability_sustainable,
            "median_depletion_age": mc.median_depletion_age,
            "p10_depletion_age": mc.p10_depletion_age,
            "p90_depletion_age": mc.p90_depletion_age,
        }
    return d


@click.command()
@click.argument("ledger_file", type=click.Path(exists=True))
@click.option("--owner", "-o", default=None, help="Filter to a specific owner name.")
@click.option("--spending-ratio", default=0.80, type=float, show_default=True,
              help="Fraction of baseline spending in retirement.")
@click.option("--return-rate", default=0.07, type=float, show_default=True,
              help="Annual portfolio return rate.")
@click.option("--inflation-rate", default=0.03, type=float, show_default=True,
              help="Annual inflation rate.")
@click.option("--retirement-age-override", default=None, type=int,
              help="Override retirement age for all owners.")
@click.option("--life-expectancy", default=100, type=int, show_default=True,
              help="Age to project through (simulation horizon).")
@click.option("--return-stddev", default=0.12, type=float, show_default=True,
              help="Annual return standard deviation for Monte Carlo simulation.")
@click.option("--spending-years", default=3, type=int, show_default=True,
              help="Number of trailing years to average for the spending baseline.")
@click.option("--tax-rate", default=0.0, type=float, show_default=True,
              help="Marginal income tax rate on traditional/HSA withdrawals (0 = no tax adjustment).")
@click.option("--monte-carlo", is_flag=True, default=False, help="Run Monte Carlo simulation.")
@click.option("--simulation-count", default=1000, type=int, show_default=True,
              help="Number of Monte Carlo simulations.")
@click.option("--json", "output_json", is_flag=True, default=False,
              help="Output machine-readable JSON.")
@click.option("--scenario", multiple=True, metavar="KEY=VALUE",
              help="What-if overrides, e.g. --scenario spending-ratio=0.70")
@click.option("--as-of-date", default=None, metavar="YYYY-MM-DD",
              help="Override the reference date for spending and contribution windows (default: today).")
@click.option("--detail", "show_detail", is_flag=True, default=False,
              help="Show year-by-year drawdown table.")
def main(
    ledger_file,
    owner,
    spending_ratio,
    return_rate,
    inflation_rate,
    tax_rate,
    life_expectancy,
    return_stddev,
    spending_years,
    retirement_age_override,
    monte_carlo,
    simulation_count,
    output_json,
    scenario,
    as_of_date,
    show_detail,
):
    """Project retirement outcomes from a Beancount ledger."""
    # Parse --scenario overrides
    scenarios: dict[str, str] = {}
    for s in scenario:
        key, _, value = s.partition("=")
        scenarios[key.strip()] = value.strip()

    if "spending-ratio" in scenarios:
        spending_ratio = float(scenarios["spending-ratio"])
    if "return-rate" in scenarios:
        return_rate = float(scenarios["return-rate"])
    if "inflation-rate" in scenarios:
        inflation_rate = float(scenarios["inflation-rate"])
    if "retirement-age" in scenarios:
        retirement_age_override = int(scenarios["retirement-age"])
    if "life-expectancy" in scenarios:
        life_expectancy = int(scenarios["life-expectancy"])
    if "return-stddev" in scenarios:
        return_stddev = float(scenarios["return-stddev"])
    if "spending-years" in scenarios:
        spending_years = int(scenarios["spending-years"])
    if "tax-rate" in scenarios:
        tax_rate = float(scenarios["tax-rate"])

    config = ProjectionConfig(
        spending_ratio=Decimal(str(spending_ratio)),
        annual_return_rate=Decimal(str(return_rate)),
        inflation_rate=Decimal(str(inflation_rate)),
        simulation_count=simulation_count,
        return_stddev=return_stddev,
        life_expectancy=life_expectancy,
        marginal_tax_rate=Decimal(str(tax_rate)),
    )

    reference_date = date.fromisoformat(as_of_date) if as_of_date else None
    base_year = reference_date.year if reference_date else date.today().year
    data = parse_ledger(ledger_file, spending_years=spending_years, inflation_rate=config.inflation_rate, today=reference_date)
    owners = data["owners"]
    accounts = data["accounts"]
    contributions = data["contributions"]
    spending = data["spending"]

    if not owners:
        console.print("[red]No owner directives found in ledger.[/red]")
        sys.exit(1)

    # Apply retirement-age override and validate --owner filter
    if owner is not None and owner not in owners:
        console.print(f"[red]Owner '{owner}' not found. Available: {', '.join(owners)}[/red]")
        sys.exit(1)

    all_owners = {
        name: (replace(o, retirement_age=retirement_age_override) if retirement_age_override is not None else o)
        for name, o in owners.items()
    }

    config_dict: dict[str, object] = {
        "spending_ratio": spending_ratio,
        "annual_return_rate": return_rate,
        "inflation_rate": inflation_rate,
        "marginal_tax_rate": tax_rate,
        "life_expectancy": life_expectancy,
        "return_stddev": return_stddev,
        "spending_years": spending_years,
    }
    spending_dict: dict[str, object] = {
        "annual_amount": float(spending.annual_amount),
        "years_averaged": spending.years_averaged,
        "inflation_adjusted": spending.inflation_adjusted,
    }

    if owner is not None:
        # Per-owner mode: single owner projected against the full household spending baseline
        per_owner_result = project_owner(
            owner=all_owners[owner],
            accounts=accounts,
            contributions=contributions,
            spending=spending,
            config=config,
            today=reference_date,
            run_monte_carlo=monte_carlo,
        )
        if output_json:
            click.echo(json_module.dumps({
                "schema_version": "1.1",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "ledger_file": ledger_file,
                "mode": "per_owner",
                "config": config_dict,
                "spending_baseline": spending_dict,
                "projections": [result_to_dict(per_owner_result, config.inflation_rate, base_year)],
            }, indent=2))
        else:
            console.print()
            console.print(render_result(
                per_owner_result, life_expectancy=config.life_expectancy, tax_rate=config.marginal_tax_rate
            ))
            if show_detail:
                show_taxes = config.marginal_tax_rate > Decimal("0")
                if per_owner_result.accumulation_rows:
                    console.print()
                    console.print(render_detail_table(
                        per_owner_result.accumulation_rows,
                        title=f"{owner.title()} — Accumulation (today → retirement)",
                        inflation_rate=config.inflation_rate,
                        base_year=base_year,
                    ))
                console.print()
                console.print(render_detail_table(
                    per_owner_result.detail_rows,
                    title=f"{owner.title()} — Drawdown (retirement → age {config.life_expectancy})",
                    inflation_rate=config.inflation_rate,
                    base_year=base_year,
                    show_taxes=show_taxes,
                ))
            console.print()
    else:
        # Household mode (default): all owners, spending counted once
        household = project_household(
            owners=list(all_owners.values()),
            accounts=accounts,
            contributions=contributions,
            spending=spending,
            config=config,
            today=reference_date,
            run_monte_carlo=monte_carlo,
        )
        if output_json:
            click.echo(json_module.dumps({
                "schema_version": "1.1",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "ledger_file": ledger_file,
                "mode": "household",
                "config": config_dict,
                "spending_baseline": spending_dict,
                "household": {
                    **household_result_to_dict(household, all_owners),
                    "accumulation": detail_rows_to_list(household.accumulation_rows, config.inflation_rate, base_year),
                    "detail": detail_rows_to_list(household.detail_rows, config.inflation_rate, base_year),
                },
            }, indent=2))
        else:
            console.print()
            console.print(render_household_result(household, all_owners, tax_rate=config.marginal_tax_rate))
            if show_detail:
                show_taxes = config.marginal_tax_rate > Decimal("0")
                if household.accumulation_rows:
                    console.print()
                    console.print(render_detail_table(
                        household.accumulation_rows,
                        title="Household — Accumulation (today → first retirement)",
                        inflation_rate=config.inflation_rate,
                        base_year=base_year,
                    ))
                console.print()
                console.print(render_detail_table(
                    household.detail_rows,
                    title=f"Household — Drawdown (first retirement → youngest age {config.life_expectancy})",
                    inflation_rate=config.inflation_rate,
                    base_year=base_year,
                    show_taxes=show_taxes,
                ))
            console.print()
