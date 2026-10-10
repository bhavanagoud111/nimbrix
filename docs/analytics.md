# Cost Analytics, Budgets, and Recommendations

Everything in this document is deterministic code (`modules/costs`, `modules/budgets`,
`modules/recommendations`). No LLM is involved in any number shown.

## Time and money conventions

| Topic | Rule |
| --- | --- |
| Time zone | UTC everywhere. A record belongs to the UTC date of its `usage_start`. |
| Day completeness | The current UTC day is always treated as partial. `as_of` = `min(latest usage_date, today − 1)`. Default windows end at `as_of`. |
| Default windows | Summary/breakdown: last 30 days to `as_of`. Time series: last 90 days. Max 400 days. |
| Month | Calendar month `[1st, 1st of next month)`. A month bucket that ends before month end is flagged `partial_last_bucket`. |
| Month-to-date comparison | `1st..as_of` vs the previous month `1st..same day-of-month` (clipped to that month's length). |
| Prior period | The window of equal length immediately before the selected window. |
| Money | `Decimal` end to end, `NUMERIC(18,6)` in PostgreSQL, strings in JSON. Rounding to cents happens only for savings estimates (half-up). |
| Currency | Aggregates are computed for one currency (`currency=USD` by default). Other currencies are reported separately in `other_currencies` and are never converted or added. |
| Allocation | Spend with a `team` tag is allocated; the rest is `(unallocated)`. Filter `team=(unallocated)` selects it. |
| Gaps | Time series emit zero for days with no records inside the window. |

## Endpoints

`GET /costs/summary`, `/costs/timeseries`, `/costs/breakdown`, `/costs/unit-costs`,
`/costs/dimensions` — all accept `start`, `end`, `account_id`, `service`, `region`,
`environment`, `team`, `tag_key`, `tag_value`, `currency`.

**Unit economics:** `/costs/unit-costs` divides cost by the provider's usage quantity per
`(service, usage_unit)` where a quantity exists (e.g. $/GB-Mo for S3). Business unit
economics (cost per customer or request) need an external metric feed and are not computed.

## Budgets

Monthly budgets scoped to total, account, service, team, or environment. For the month of
`as_of`:

- **actual** = month-to-date spend in the budget currency;
- **projected** = actual + persisted ML forecast for the rest of the month when a forecast
  for that scope trained through `as_of` exists (`projection_method: forecast`), otherwise
  the run rate `actual / elapsed days × days in month` (`run_rate`);
- **status**: `over_budget` (actual ≥ 100%), `at_risk` (projected ≥ 100%), `warning` (an alert
  threshold crossed), else `on_track`.

The analysis job raises one alert per (budget, month, threshold, actual|forecast) the first
time it is crossed; repeat runs do not duplicate alerts.

## Recommendation rules (`rules-v1`)

| Category | Eligibility (all must hold) | Estimated monthly savings | Confidence / risk | Action |
| --- | --- | --- | --- | --- |
| `idle_nonprod_instance` | EC2 running; env staging/dev/test/sandbox; not tagged `nimbrix:protected=true`; ≥14 days hourly CPU with ≥90% datapoints; avg < 5% and p95 < 10% | `hourly × 730` | high if coverage ≥ 95% and avg < 2%, else medium / low | `simulate_stop_nonprod_instance` |
| `underutilized_compute` | EC2 running; complete CPU evidence; avg < 20% and p95 < 40%; a one-size-smaller type exists; not already idle-nonprod | `(current − target hourly) × 730` | medium / high (prod) or medium | manual only |
| `unattached_volume` | EBS `available`; detached ≥ 7 days when the detach time is known | `size × $/GB-month` | high if age known, else medium / medium | manual only |
| `unused_elastic_ip` | Elastic IP not associated | `$0.005 × 730` | high / low | manual only |
| `nonprod_scheduling` | Non-prod EC2 running, not idle, ≥80% of CPU activity in weekday business hours | `hourly × 730 × 108/168` | medium / low | manual only |

**Insufficient evidence.** When the signal a rule needs is missing — no CPU metrics, a window
shorter than 14 days, < 90% datapoint coverage, or no list price — the rule stores a finding
with `status = insufficient_evidence`, `confidence = insufficient_evidence`, and **no savings
figure**. Scheduling is not evaluated at all without hourly-pattern evidence.

Each recommendation stores the eligibility checks (observed vs required), evidence (inventory
snapshot, utilization window, prices), assumptions, savings formula, owner (team tag or
`unassigned`), confidence, and risk. Refreshes update evidence but never override a user's
`dismissed`, `approval_requested`, or `actioned` status; findings that no longer apply become
`superseded`.

## Simulation

`POST /recommendations/{id}/simulate` changes nothing. It returns:

- `current_monthly_cost`: list price × 730 (or the volume's monthly price);
- `projected_monthly_cost`: current − estimated savings;
- `savings_range`: spans the list-price estimate and the **observed billed run rate** (last 7
  billed days × 365/12), widened by ±5% (high), ±15% (medium), or ±30% (low confidence);
- `excluded_costs`: e.g. attached EBS storage that keeps billing, data transfer, snapshots;
- `assumptions`, `confidence`, and the fixed labels `mode: "simulation"`, `realized: false`.
