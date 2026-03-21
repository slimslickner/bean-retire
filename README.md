# bean-retire

Retirement projection tool for [Beancount](https://github.com/beancount/beancount) ledgers. Reads your existing plain-text accounting file and projects retirement outcomes — portfolio growth, drawdown, Social Security income, and Monte Carlo survival probability — without any manual data entry.

## Installation

Requires Python 3.11+.

```bash
uv add git+https://github.com/slimslickner/bean-retire
```

Or for development:

```bash
git clone https://github.com/slimslickner/bean-retire
cd bean-retire
uv sync
```

## Ledger Setup

bean-retire reads directly from your Beancount ledger. Two things must be configured before running.

### 1. Owner directives

Add one `custom "owner"` directive per person. The first four metadata fields are required; pension fields are optional.

```beancount
2020-01-01 custom "owner" "person1"
  birth-date: "1985-03-15"
  retirement-age: "57"
  social-security-age: "67"
  social-security-estimate: "2400"
  pension-age: "57"
  pension-estimate: "1000"

2020-01-01 custom "owner" "person2"
  birth-date: "1987-06-20"
  retirement-age: "55"
  social-security-age: "67"
  social-security-estimate: "1800"
```

| Field | Required | Description |
|---|---|---|
| `birth-date` | Yes | ISO date `YYYY-MM-DD` |
| `retirement-age` | Yes | Age (integer) at which this person plans to retire |
| `social-security-age` | Yes | Age to begin Social Security benefits |
| `social-security-estimate` | Yes | Monthly SS benefit in USD (from SSA statement) |
| `pension-age` | No | Age at which pension payments begin |
| `pension-estimate` | No | Monthly pension income in USD |

Pension income is modelled identically to Social Security: it starts at `pension-age`, is inflation-adjusted each year, and reduces portfolio withdrawals accordingly.

### 2. Retirement account metadata

Tag each retirement account `Open` directive with `account-owner` and `tax-account-type`.

```beancount
2020-01-01 open Assets:Investment:Retirement:Person1s-401k USD
  account-owner: "person1"
  tax-account-type: "split"
  traditional-percent: "65"
  roth-percent: "35"

2020-01-01 open Assets:Investment:Retirement:Person1s-Roth-IRA USD
  account-owner: "person1"
  tax-account-type: "roth"

2020-01-01 open Assets:Investment:Retirement:Person2s-403b USD
  account-owner: "person2"
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

#### Commodity sub-accounts (one account per fund)

Many Beancount users model a 401k or brokerage as a parent account with one child account per fund or commodity:

```
Assets:Investment:Retirement:CapTech-Capital-Group-401k
Assets:Investment:Retirement:CapTech-Capital-Group-401k:Cash
Assets:Investment:Retirement:CapTech-Capital-Group-401k:RBFGX
Assets:Investment:Retirement:CapTech-Capital-Group-401k:VFIAX
Assets:Investment:Retirement:CapTech-Capital-Group-401k:VTMGX
```

**Only the parent account needs metadata.** Sub-accounts require no `account-owner` or `tax-account-type` — bean-retire aggregates them automatically.

```beancount
; Tag the parent with metadata
2020-01-01 open Assets:Investment:Retirement:CapTech-Capital-Group-401k
  account-owner: "person1"
  tax-account-type: "traditional"

; Sub-accounts: no metadata needed
2020-01-01 open Assets:Investment:Retirement:CapTech-Capital-Group-401k:Cash  USD
2020-01-01 open Assets:Investment:Retirement:CapTech-Capital-Group-401k:RBFGX RBFGX
2020-01-01 open Assets:Investment:Retirement:CapTech-Capital-Group-401k:VFIAX VFIAX
2020-01-01 open Assets:Investment:Retirement:CapTech-Capital-Group-401k:VTMGX VTMGX
```

**Balance**: bean-retire walks the full sub-account tree and converts each position to USD. It uses the most recent `price` directive on or before the reference date; if no price is available, it falls back to the cost basis recorded in the transaction.

**Contributions**: any positive posting to a sub-account (e.g. purchasing fund shares) is attributed to the parent tagged account and valued at cost basis (`units × cost-per-unit`). Add `price` directives to your ledger to keep balance valuations current — they do not affect contribution accounting.

```beancount
; Prices used for balance valuation (update periodically)
2025-01-01 price VFIAX 125.00 USD
2025-01-01 price VTMGX  62.00 USD

; Contribution recorded as a fund share purchase — valued at cost basis
2025-06-15 * "401k contribution"
  Assets:Investment:Retirement:CapTech-Capital-Group-401k:VFIAX  10 VFIAX {125.00 USD}
  Assets:Investment:Retirement:CapTech-Capital-Group-401k:VTMGX  20 VTMGX {62.00 USD}
  Income:Salary:Gross                                          -2490.00 USD
```

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

Schema version `1.1`. Stable interface for downstream tools and LLM integration.

### Household mode (default)

```json
{
  "schema_version": "1.1",
  "generated_at": "2026-03-20T14:00:00",
  "ledger_file": "ledger.beancount",
  "mode": "household",
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
  "household": {
    "owners": ["person1", "person2"],
    "first_retirement_date": "2042-03-15",
    "first_retirement_age": 57,
    "combined_portfolio_at_first_retirement": 2100000.0,
    "annual_income_need": 35656.62,
    "ss_income_by_owner": {
      "person1": {"annual_amount": 28800.0, "social_security_age": 67},
      "person2": {"annual_amount": 21600.0, "social_security_age": 67}
    },
    "total_annual_ss_income": 50400.0,
    "pension_income_by_owner": {
      "person1": {"annual_amount": 12000.0, "pension_age": 57},
      "person2": {"annual_amount": 0.0, "pension_age": null}
    },
    "total_annual_pension_income": 12000.0,
    "years_to_depletion": null,
    "depletion_age": null,
    "sustainable": true,
    "monte_carlo": null
  }
}
```

### Per-owner mode (`--owner NAME`)

```json
{
  "schema_version": "1.1",
  "generated_at": "2026-03-20T14:00:00",
  "ledger_file": "ledger.beancount",
  "mode": "per_owner",
  "config": { "...": "..." },
  "spending_baseline": { "...": "..." },
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
      "annual_pension_income": 12000.0,
      "years_retirement_to_pension": 0,
      "annual_portfolio_withdrawal_need": 23656.62,
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

### Household mode (default)

Running `bean-retire` without `--owner` projects the entire household together:

- **Spending is counted once** — the household spending baseline is not duplicated per owner.
- **Accumulation phase** (today → first retirement date): each owner's portfolio grows independently at `annual_return_rate`, with annual contributions (derived from the trailing 2-year average of inflows to tagged accounts) added at year-end. All portfolios are merged into a single combined pool at the first owner's retirement date.
- **Overlap period**: if some owners retire before others, the still-working owners' contributions continue flowing into the combined pool, reducing drawdown pressure.
- **Joint drawdown phase** (first retirement → youngest owner's age 100): each year, inflation-adjusted household spending minus stacked Social Security income is withdrawn from the combined pool. Social Security income from each owner is added once that owner reaches their `social-security-age`.

### Per-owner mode (`--owner NAME`)

Passing `--owner` projects a single owner independently against the full household spending baseline — equivalent to the previous default behavior. Useful for understanding each person's individual retirement trajectory.

**Spending baseline**: trailing 3-year average of `Expenses:*` postings, with each year's total inflated to today's dollars using `inflation_rate`. Multiplied by `spending_ratio` (default 0.80) to get the retirement income target.

## Development

```bash
uv run pytest tests/ -v
```

Tests use a self-contained fixture ledger at `tests/fixtures/sample.beancount` and do not mock the Beancount API.
