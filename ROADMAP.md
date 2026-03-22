# Roadmap

## 1. Debug: contribution miscounting

Contributions are being overcounted relative to expected values. Most likely suspects, in order of probability:

- **Rollovers or one-time transfers** recorded in the trailing N-year window — a rollover looks identical to a contribution (large external-origin posting into a retirement account)
- **Double-counting across transaction legs** — e.g. paycheck → checking → 401k recorded as two transactions that each have a positive posting to the retirement tree
- **Short averaging window** — a one-time large event in the window dominates the average; increasing `--contribution-years` may smooth it out
- **Employer match recorded as a separate transaction** from the same external source, counted again as a second contribution

To diagnose: inspect actual transactions posting to retirement accounts in the trailing window.

## 2. Configurability & parameterization

### Contribution controls
- `--exclude-account ACCOUNT` — exclude specific accounts from contribution totals (handles rollovers, one-time events, accounts that shouldn't be projected)
- `--contribution-override NAME=AMOUNT` — manually set annual contribution for an owner, bypassing ledger calculation entirely
- `--contribution-years N` — already a parser parameter, should be exposed as a CLI flag

### Tax model
- Apply tax to SS income (85% taxable fraction) when `--tax-rate > 0`
- Apply tax to pension income (100% taxable) when `--tax-rate > 0`
- Both discussed but not yet implemented

### Account types
- `taxable` brokerage type — currently skipped with a warning; relevant for early retirees drawing from taxable accounts before 59½
- `pension` DB account type — currently skipped with a warning; balance is irrelevant for a defined-benefit plan but the warning is noise
