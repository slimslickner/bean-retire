# bean-retire

Retirement projection tool for [Beancount](https://github.com/beancount/beancount) ledgers. Reads your existing plain-text accounting file and projects retirement outcomes — portfolio growth, drawdown, Social Security income, and Monte Carlo survival probability — without any manual data entry.

## Installation

Requires Python 3.11+.

```bash
pip install bean-retire
```

Or for development:

```bash
git clone https://github.com/yourname/bean-retire
cd bean-retire
uv sync
```

## Ledger Setup

bean-retire reads directly from your Beancount ledger. Two things must be configured before running.

### 1. Owner directives

Add one `custom "owner"` directive per person. All five metadata fields are required.

```beancount
2020-01-01 custom "owner" "person1"
  birth-date: "1985-03-15"
  retirement-age: "57"
  social-security-age: "67"
  social-security-estimate: "2400"

2020-01-01 custom "owner" "person2"
  birth-date: "1987-06-20"
  retirement-age: "55"
  social-security-age: "67"
  social-security-estimate: "1800"
```

| Field | Description |
|---|---|
| `birth-date` | ISO date `YYYY-MM-DD` |
| `retirement-age` | Age (integer) at which this person plans to retire |
| `social-security-age` | Age to begin Social Security benefits |
| `social-security-estimate` | Monthly SS benefit in USD (from SSA statement) |

### 2. Retirement account metadata

Tag each retirement account `Open` directive with `owner` and `tax-account-type`.

```beancount
2020-01-01 open Assets:Investment:Retirement:Person1s-401k USD
  owner: "person1"
  tax-account-type: "split"
  traditional-percent: "65"
  roth-percent: "35"

2020-01-01 open Assets:Investment:Retirement:Person1s-Roth-IRA USD
  owner: "person1"
  tax-account-type: "roth"

2020-01-01 open Assets:Investment:Retirement:Person2s-403b USD
  owner: "person2"
  tax-account-type: "traditional"
```

Valid `tax-account-type` values:

| Value | Meaning |
|---|---|
| `traditional` | Pre-tax (401k, 403b, Traditional IRA) |
| `roth` | Post-tax Roth |
| `hsa` | Health Savings Account |
| `split` | Mixed pre/post-tax; requires `traditional-percent` and `roth-percent` |

For `split` accounts, percentages must sum to 100.

### 3. Expense accounts (no setup needed)

bean-retire derives the spending baseline automatically from your `Expenses:*` postings over the trailing 3 years. No additional configuration is required.

## Usage

```
bean-retire LEDGER_FILE [OPTIONS]
```

### Basic

```bash
bean-retire ledger.beancount
```

### Filter to one owner

```bash
bean-retire ledger.beancount --owner person1
```

### Override assumptions

```bash
bean-retire ledger.beancount \
  --spending-ratio 0.75 \
  --return-rate 0.06 \
  --inflation-rate 0.025 \
  --retirement-age-override 55
```

### What-if scenarios

```bash
bean-retire ledger.beancount \
  --scenario retirement-age=52 \
  --scenario spending-ratio=0.70
```

`--scenario` accepts the same keys as the explicit flags and takes precedence over them.

### Monte Carlo

```bash
bean-retire ledger.beancount --monte-carlo --simulation-count 2000
```

Runs N simulations with normally-distributed annual returns (mean = `--return-rate`, stddev = 12%) and reports probability of portfolio survival to age 100, plus 10th/90th percentile depletion ages.

### Historical ledger

If your ledger covers past years (e.g., a demo file from 2013–2015), anchor the spending window with `--as-of-date`:

```bash
bean-retire ledger.beancount --as-of-date 2016-01-01
```

### Machine-readable output

```bash
bean-retire ledger.beancount --json
```

## JSON output schema

Schema version `1.0`. Stable interface for downstream tools and LLM integration.

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-03-20T14:00:00",
  "ledger_file": "ledger.beancount",
  "config": {
    "spending_ratio": 0.8,
    "annual_return_rate": 0.07,
    "inflation_rate": 0.03
  },
  "spending_baseline": {
    "annual_amount": 44570.78,
    "years_averaged": 3,
    "inflation_adjusted": true
  },
  "projections": [
    {
      "owner": "person1",
      "retirement_date": "2042-03-15",
      "retirement_age": 57,
      "social_security_age": 67,
      "years_retirement_to_ss": 10,
      "portfolio_at_retirement": 1455934.0,
      "annual_income_need": 35656.62,
      "annual_ss_income": 28800.0,
      "annual_portfolio_withdrawal_need": 35656.62,
      "years_to_depletion": null,
      "depletion_age": null,
      "sustainable": true,
      "monte_carlo": null
    }
  ]
}
```

`monte_carlo` is `null` unless `--monte-carlo` is passed, in which case:

```json
"monte_carlo": {
  "probability_sustainable": 0.83,
  "median_depletion_age": null,
  "p10_depletion_age": 78,
  "p90_depletion_age": null
}
```

`median_depletion_age` and percentile fields are `null` when all (or most) simulations are sustainable.

## Validation

bean-retire checks the following and prints warnings to stderr:

- Owner directive is missing required metadata fields
- `tax-account-type` is not a recognised value
- SPLIT account percentages do not sum to 100
- A retirement account references an owner name not defined by any `custom "owner"` directive
- An owner has no retirement accounts tagged in the ledger
- No `Expenses:*` transactions found in the trailing N-year window (spending baseline will be $0)

Beancount's own loader errors are also printed to stderr.

## How projections work

**Accumulation phase** (today → retirement date): current portfolio grows at `annual_return_rate`, with annual contributions (derived from the trailing 2-year average of inflows to tagged accounts) added at year-end.

**Drawdown phase** (retirement → age 100): each year, spending need is inflated by `inflation_rate`. Social Security income (also inflation-adjusted) is subtracted once the owner reaches `social-security-age`. The remainder is withdrawn from the portfolio, which then earns `annual_return_rate` on what remains.

**Spending baseline**: trailing 3-year average of `Expenses:*` postings, with each year's total inflated to today's dollars using `inflation_rate`. Multiplied by `spending_ratio` (default 0.80) to get the retirement income target.

## Development

```bash
uv run pytest tests/ -v
```

Tests use a self-contained fixture ledger at `tests/fixtures/sample.beancount` and do not mock the Beancount API.
