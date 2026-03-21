import json as json_module
import sys
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import click
from rich.console import Console
from rich.panel import Panel

from .models import HouseholdProjectionResult, Owner, ProjectionConfig, ProjectionResult
from .parser import parse_ledger
from .projection import project_household, project_owner

console = Console()


def fmt_dollars(amount: Decimal) -> str:
    return f"${amount:,.0f}"


def fmt_pct(rate: float) -> str:
    return f"{rate:.1%}"


def render_result(result: ProjectionResult) -> Panel:
    lines = []

    sustainable = result.years_to_depletion is None
    if sustainable:
        outcome_icon = "[green]✓ Sustainable to age 100[/green]"
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
    withdrawal = fmt_dollars(result.annual_portfolio_withdrawal_need)
    lines.append(f"[bold]Portfolio withdrawal:[/bold]  {withdrawal}/yr (year 1)")
    lines.append("")
    lines.append(f"[bold]Outcome:[/bold]  {outcome_icon}")

    if result.monte_carlo_result is not None:
        mc = result.monte_carlo_result
        lines.append("")
        lines.append(f"[bold]Monte Carlo ({result.simulation_count:,} simulations):[/bold]")
        lines.append(f"  Probability sustainable to 100:  {fmt_pct(mc.probability_sustainable)}")
        if mc.median_depletion_age is not None:
            lines.append(f"  Median depletion age:           {mc.median_depletion_age}")
        if mc.p10_depletion_age is not None:
            lines.append(f"  10th / 90th percentile age:     {mc.p10_depletion_age} / {mc.p90_depletion_age}")

    return Panel(
        "\n".join(lines),
        title=f"[bold cyan]{result.owner.title()}[/bold cyan]",
        expand=False,
    )


def result_to_dict(result: ProjectionResult) -> dict[str, object]:
    d: dict[str, object] = {
        "owner": result.owner,
        "retirement_date": result.retirement_date.isoformat(),
        "retirement_age": result.retirement_age,
        "social_security_age": result.social_security_age,
        "years_retirement_to_ss": result.years_retirement_to_ss,
        "portfolio_at_retirement": float(result.portfolio_at_retirement),
        "annual_income_need": float(result.annual_income_need),
        "annual_ss_income": float(result.annual_ss_income),
        "annual_portfolio_withdrawal_need": float(result.annual_portfolio_withdrawal_need),
        "years_to_depletion": result.years_to_depletion,
        "depletion_age": result.depletion_age,
        "sustainable": result.years_to_depletion is None,
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


def render_household_result(result: HouseholdProjectionResult, owners: dict[str, Owner]) -> Panel:
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
        "years_to_depletion": result.years_to_depletion,
        "depletion_age": result.depletion_age,
        "sustainable": result.years_to_depletion is None,
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
@click.option("--monte-carlo", is_flag=True, default=False, help="Run Monte Carlo simulation.")
@click.option("--simulation-count", default=1000, type=int, show_default=True,
              help="Number of Monte Carlo simulations.")
@click.option("--json", "output_json", is_flag=True, default=False,
              help="Output machine-readable JSON.")
@click.option("--scenario", multiple=True, metavar="KEY=VALUE",
              help="What-if overrides, e.g. --scenario spending-ratio=0.70")
@click.option("--as-of-date", default=None, metavar="YYYY-MM-DD",
              help="Override the reference date for spending and contribution windows (default: today).")
def main(
    ledger_file,
    owner,
    spending_ratio,
    return_rate,
    inflation_rate,
    retirement_age_override,
    monte_carlo,
    simulation_count,
    output_json,
    scenario,
    as_of_date,
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

    config = ProjectionConfig(
        spending_ratio=Decimal(str(spending_ratio)),
        annual_return_rate=Decimal(str(return_rate)),
        inflation_rate=Decimal(str(inflation_rate)),
        simulation_count=simulation_count,
    )

    reference_date = date.fromisoformat(as_of_date) if as_of_date else None
    data = parse_ledger(ledger_file, today=reference_date)
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
                "projections": [result_to_dict(per_owner_result)],
            }, indent=2))
        else:
            console.print()
            console.print(render_result(per_owner_result))
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
                "household": household_result_to_dict(household, all_owners),
            }, indent=2))
        else:
            console.print()
            console.print(render_household_result(household, all_owners))
            console.print()
