# Trend and variance analysis

## Why this layer exists

A chart can suggest that sales fell. It rarely says against what. This layer
makes the comparison explicit: which metric, over which period, against which
baseline, by how much in absolute terms, and by what percentage.

It answers "what moved and by how much". It does not answer "why", and it is
careful not to imply that it does. Attribution is the job of driver
decomposition, which consumes these records.

## Period handling

### Periods

| Period | Definition | Identified by |
| --- | --- | --- |
| `day` | One calendar day | The date |
| `week` | Monday to Sunday | Its Monday |
| `month` | Calendar month | The 1st |

### Partial periods are excluded by default

This is the most important correctness rule in the layer.

A period comparison is only meaningful between periods of equal length. The
sample range runs 2026-01-01 (a Thursday) to 2026-03-01, so:

- the first week holds **4 days**, not 7
- March holds **1 day**, not 31

Comparing those against full periods reports a change in calendar coverage as
though it were a change in trade. Concretely, including the partial first week
produces a **+72% week-over-week surge** into week two. Nothing surged; the
first week was simply short. With partial periods excluded, real weekly
movement is between −9% and +6%.

Every period is therefore labelled complete or partial. Completeness is judged
against the calendar, not against how many rows are present, so a genuine
zero-trade day never makes a period look partial. `include_partial_periods=True`
opts back in when a caller genuinely wants period-to-date figures.

### Metrics are recomputed at the target grain

Period metrics are not rolled up from daily values. `summarise_by_period`
aggregates the facts directly at the period grain through the same
`aggregate_commercial_metrics` helper the daily commercial mart uses, so there
is one definition of every metric rather than two.

This matters most for ratios. Averaging daily fill rates into a weekly figure
weights every day equally regardless of the demand behind it:

| Day | Ordered | Fulfilled | Daily fill rate |
| --- | --- | --- | --- |
| Monday | 100 | 50 | 0.500 |
| Tuesday | 1 | 1 | 1.000 |
| **Week** | **101** | **51** | **0.505** |

The correct weekly fill rate is 51/101 = 0.505. The average of the daily rates
is 0.750, which describes a week that did not happen. Order counts are computed
with a distinct count at the period grain for the same reason, so a multi-SKU
order is never counted more than once.

## Trend records

`calculate_trend` compares each period against an earlier one within the same
segment, returning:

| Field | Meaning |
| --- | --- |
| `metric` | Which metric moved |
| `period_type`, `period_start` | The period being described |
| `previous_period_start` | What it is compared against |
| `value`, `previous_value` | The two figures |
| `absolute_change` | Movement in metric units |
| `pct_change` | Movement as a share of the prior value |

`pct_change` is undefined (NaN) when the prior value is zero, rather than
reported as an infinite change.

## Variance records

All three comparisons emit **the same schema**. That uniformity is deliberate:
downstream layers should be able to consume "this metric is below expectation"
without caring where the expectation came from.

```
metric, segment, period_type, period_start, comparison_period,
actual, comparison_value, variance, variance_pct, comparison_type
```

plus the individual dimension columns, so records stay groupable.

`variance = actual − comparison_value`, and
`variance_pct = variance ÷ comparison_value`, undefined against a zero baseline.

| `comparison_type` | Baseline | Function |
| --- | --- | --- |
| `previous_period` | The same segment, an earlier period | `variance_vs_previous_period` |
| `target` | Plan, at a matching grain | `variance_vs_target` |
| `forecast` | Model expectation over the same dates | `variance_vs_forecast` |

`combine_variances` stacks them and rejects any frame missing the schema, so a
drifting producer fails loudly rather than contributing malformed evidence.

### Target aggregation, and one stated assumption

Targets are aggregated to the actuals' grain before merging, so no fact is ever
joined to another fact at an incompatible grain.

| Target | Aggregation | Why |
| --- | --- | --- |
| `sales_target` | Sum | A quantity |
| `order_target` | Sum | A quantity |
| `fill_rate_target` | **Mean** | A rate, not a quantity |

Summing two daily fill-rate targets of 0.90 and 0.80 would produce 1.70, which
is not a rate at all. **Averaging is an assumption, not a derivation.** A
demand-weighted period target would be better, but `fact_targets` carries no
target demand to weight by. The assumption is recorded here because the number
is used downstream.

### Forecast variance is total-level only

The forecasting layer is evaluated at total daily grain. Segment-level forecast
variance requires segment-level forecasts and must not be inferred from the
total comparison.

## Ranking

`rank_variances` orders records by the size of the gap. It reports which gaps
are **largest**, not which are most important and not what caused them. A large
variance in a small segment may matter less than a modest one in a large
segment; weighing that is the job of the impact layer.

## A note on the sample targets

The original generator set `sales_target` to a flat 15,000 per region and
category and `order_target` to 80 per region and category, neither derived from
realised volume. Company-wide that produced a daily sales target of 241,741
against actual sales near 63,500, and an order target of 1,280 against 80 actual
orders. Every segment therefore missed target by 65% to 93% on every day, and
target variance carried no information: with everything failing equally, nothing
stands out.

`channel` was also a constant `"ALL"` placeholder, so the documented
date × region × category × channel grain was not real and any channel-level
target comparison would have silently matched nothing.

Targets are now anchored to each segment's realised daily volume, with a
persistent per-segment plan bias, and carry real channels. Overall attainment is
**97.4%**, with segment attainment spanning **85.1% to 119.0%** — 23 segments
above plan and 41 below. That spread is what makes plan-versus-actual analysis
worth running.

This changed `fact_targets` from 960 to 3,840 rows. Sales, orders, and inventory
are untouched, and forecast accuracy figures are unchanged.

## Worked example: the seeded supply disruption

From 2026-02-18 the Bengaluru fruit and vegetable segments fulfil a much smaller
share of demand. Weekly fill rate for BLR Fruits:

| Week of | Fill rate | Change |
| --- | --- | --- |
| 2026-02-02 | 0.949 | +0.009 |
| 2026-02-09 | 0.994 | +0.045 |
| 2026-02-16 | 0.795 | **−0.199** |
| 2026-02-23 | 0.693 | **−0.102** |

Against the 0.93 service-level target, BLR Vegetables ran between +0.01 and
+0.06 **above** target through 2026-02-09, then fell to −0.188 and −0.227.

This is evidence that fulfilment deteriorated in a specific segment at a
specific time. It is **not** a statement of cause. Establishing whether supply,
demand, or something else drove it is the job of the layers above.

## Limitations

1. **Week-over-week on a 60-day range yields 8 complete weeks**, so seven
   comparisons. Month-over-month yields one. These are short series.
2. **`variance_vs_target` requires every grouping dimension to exist in
   `fact_targets`.** Targets carry region, category, and channel, so KAM-level,
   customer-level, and SKU-level target variance is not available and the
   function raises rather than returning empty comparisons.
3. **Rate targets are averaged across the period**, as described above.
4. **Ranking is by magnitude only.** No significance test is applied, so a large
   gap in a volatile thin segment ranks alongside a large gap in a stable one.
