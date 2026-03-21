# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run pytest tests/ -v                        # run all tests
uv run pytest tests/ -v -k test_name           # run a single test
uv run ruff check bean_retire/                 # lint
uv run ruff check --fix bean_retire/           # lint with autofix
uv run ty check bean_retire/                   # type check
bean-retire ledger.beancount                   # run after uv sync
```

## Architecture

The tool has four layers that flow strictly top-to-bottom:

**`cli.py`** — Click entrypoint. Parses flags, applies `--scenario` overrides, builds `ProjectionConfig`, calls `parse_ledger`, then routes to either `project_owner` (per-owner mode) or `project_household` (default). Also owns all Rich/JSON rendering.

**`parser.py`** — Reads a Beancount file and returns a dict with `owners`, `accounts`, `contributions`, and `spending`. No projection logic here. Three key functions: `parse_owners` (reads `custom "owner"` directives), `compute_account_balances` (uses beancount realization + price map to get USD values, recursively aggregating sub-account trees), `compute_annual_contributions` (trailing-N-year average, excludes intra-account transactions), `compute_spending_baseline` (trailing-N-year average of `Expenses:*`, inflation-adjusted to today).

**`projection.py`** — Pure calculation, no I/O. `project_owner` and `project_household` both take parsed data + `ProjectionConfig` and return result dataclasses. Household mode merges all portfolios at the first retirement date, then runs a joint drawdown with per-owner SS/pension stacking and working-spouse contributions reducing withdrawals. Both functions produce `accumulation_rows` (today → retirement) and `detail_rows` (retirement → life expectancy) for the `--detail` table.

**`models.py`** — Dataclasses only: `Owner`, `RetirementAccount`, `SpendingBaseline`, `ProjectionConfig`, `DetailRow`, `ProjectionResult`, `HouseholdProjectionResult`, `MonteCarloResult`.

## Key design details

- `ProjectionConfig` carries all tunable parameters: `spending_ratio`, `annual_return_rate`, `inflation_rate`, `simulation_count`, `return_stddev`, `life_expectancy`. When adding a new projection parameter, add it here first.
- `--scenario KEY=VALUE` overrides mirror the CLI flag names (`spending-ratio`, `return-rate`, `inflation-rate`, `retirement-age`, `life-expectancy`, `return-stddev`, `spending-years`). When adding a new flag, add a corresponding scenario override in `main()`.
- `spending_years` is a `parse_ledger()` parameter, not in `ProjectionConfig`, because it affects parsing rather than projection math.
- Sub-account aggregation: retirement accounts can have commodity sub-accounts (one per fund). Only the parent needs metadata; `_sum_subtree_balance` recursively aggregates. Contribution logic skips purely intra-account transactions to avoid double-counting fund purchases.
- Tests use `tests/fixtures/sample.beancount` — a real Beancount file, no mocks.
- JSON schema version is `"1.1"`. Increment only on breaking changes to the output shape.
