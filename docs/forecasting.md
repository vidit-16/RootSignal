# Forecasting

## Why this layer exists

Variance analysis needs something to compare against. "Sales fell 8% yesterday"
is only actionable once you know whether 8% is inside normal daily movement or
genuinely outside it. The forecast supplies that expected baseline, so a
movement can be classified as ordinary variation or as underperformance worth
investigating.

The forecast is an input to evidence, not a prediction product in its own right.

## Series construction

`build_daily_series` aggregates cleaned facts to one row per calendar day:

| Metric | Source | Definition |
| --- | --- | --- |
| `net_sales` | `fact_sales` | Sum of net sales |
| `units` | `fact_sales` | Sum of units |
| `orders` | `fact_orders` | Distinct `order_id` |

Two rules matter here:

- **Orders are counted distinctly.** `fact_sales` and `fact_orders` are
  order-line tables, so an order carrying three SKUs is one order, not three.
- **Calendar gaps are filled with zero, not dropped.** A day with no trade is a
  real observation. Dropping it would shorten the weekly cycle and misalign
  every seasonal position after it.

Forecasters reject any history that is not a gap-free daily series, rather than
silently producing a misaligned seasonal forecast.

## Models

All five models are closed-form. There is no random initialisation and no
numerical optimiser, so the same history always produces the same forecast.
That determinism is a requirement, not a convenience: forecast output feeds the
evidence package, and evidence that changes between runs is not evidence.

| Model | Method | Role |
| --- | --- | --- |
| `naive` | Carry the last observed value forward | Reference baseline |
| `moving_average_7` | Mean of the last 7 days | Second, stronger baseline |
| `ses` | Exponentially weighted level, projected flat | Level-tracking |
| `seasonal_naive_7` | Repeat the most recent week | Weekly shape, one week of evidence |
| `seasonal_mean_7` | Average each weekday across all history | Weekly shape, all evidence |

### Why no statsmodels or scikit-learn

Neither is needed. Every method above is a few lines of arithmetic, and
implementing them directly keeps the layer fully deterministic and readable.
Optimiser-fitted models would introduce solver-version-dependent results for no
accuracy gain on a 60-day series. The dependencies stay available as optional
extras for when a method genuinely requires them.

### Why seasonal methods are justified here

Not by habit, but by the data. In daily `net_sales`:

- lag-7 autocorrelation: **+0.245**
- lag-14 autocorrelation: **+0.291**
- lag-1 autocorrelation: **−0.053**

Weekly lags are the only materially positive autocorrelations, and the
day-of-week profile spans roughly 25% (Friday indexes at 1.15 of the mean,
Wednesday at 0.91). The series is mean-reverting with weekly structure, which
is exactly the shape a seasonal model captures and a last-value model cannot.

### Parameter selection

`ses` selects `alpha` by grid search over a fixed grid, minimising in-sample
one-step-ahead squared error **on the training history only**. A parameter tuned
on the evaluation window would leak the answer into the forecast and inflate
every accuracy number downstream.

## Evaluation

### Rolling-origin backtest

A single holdout on a 60-day series scores every model on one accident of
timing. `rolling_origin_evaluate` instead re-forecasts from successive origins
with an expanding training window, and pools the errors:

```
fold 1: train days 1-28  -> score days 29-35
fold 2: train days 1-35  -> score days 36-42
fold 3: train days 1-42  -> score days 43-49
fold 4: train days 1-49  -> score days 50-56
```

At every origin the model is refitted on data strictly before the window it is
scored on. `tests/test_forecasting.py` asserts this directly: corrupting every
observation from the first origin onward must leave fold 1's forecasts
byte-identical. Injecting a deliberate leak into the split makes that test fail.

Errors are pooled across folds rather than averaged per fold, so each fold is
weighted by the volume it actually carries.

### Metrics

| Metric | Definition | Notes |
| --- | --- | --- |
| MAE | Mean absolute error | In metric units |
| RMSE | Root mean squared error | Penalises large misses |
| **WAPE** | Total absolute error ÷ total actual | **Headline metric** |
| MAPE | Mean of per-day percentage errors | Guarded, see below |
| Bias | Mean signed error | Positive means forecasting high |

WAPE is the headline because it aggregates error against total volume, so it
stays interpretable when individual days are small and cannot be distorted by a
single near-zero denominator.

**MAPE is reported as undefined (NaN) when any actual falls below 1% of mean
volume.** MAPE divides by each actual in turn, so one near-zero day can dominate
the average and produce a large number that says nothing about accuracy.
Reporting NaN is honest; reporting that number would not be.

## Measured results

Rolling-origin backtest, horizon 7, initial train 28, step 7 — 4 folds,
28 scored observations per model. Reproduce with:

```bash
python scripts/evaluate_forecasts.py
```

### `net_sales`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| **seasonal_mean_7** | 5,543.42 | 7,008.72 | **0.0899** | +1,770.58 | **+23.5%** |
| ses | 6,574.87 | 7,937.66 | 0.1066 | +805.66 | +9.2% |
| moving_average_7 | 6,788.03 | 8,381.09 | 0.1101 | +567.27 | +6.3% |
| naive | 7,242.46 | 8,595.08 | 0.1174 | −833.12 | baseline |
| seasonal_naive_7 | 7,720.55 | 10,080.75 | 0.1252 | +567.27 | −6.6% |

### `units`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| **seasonal_mean_7** | 13.18 | 16.13 | **0.0417** | +4.56 | **+21.7%** |
| seasonal_naive_7 | 14.96 | 19.25 | 0.0473 | +1.61 | +11.0% |
| naive | 16.82 | 23.68 | 0.0532 | −7.04 | baseline |
| ses | 18.09 | 23.03 | 0.0573 | +0.80 | −7.6% |
| moving_average_7 | 18.96 | 23.98 | 0.0600 | +1.61 | −12.7% |

### Robustness

`seasonal_mean_7` ranks first for `net_sales` in every backtest configuration
tested, with WAPE between 0.074 and 0.099:

| horizon | initial train | step | folds | WAPE | vs naive |
| --- | --- | --- | --- | --- | --- |
| 7 | 28 | 7 | 4 | 0.0899 | +23.5% |
| 7 | 28 | 3 | 9 | 0.0893 | +37.8% |
| 7 | 21 | 3 | 11 | 0.0992 | +39.6% |
| 7 | 35 | 3 | 7 | 0.0756 | +37.6% |
| 14 | 28 | 7 | 3 | 0.0852 | +29.2% |
| 14 | 35 | 7 | 2 | 0.0740 | +39.3% |

The headline figure quoted elsewhere is the **most conservative** of these
(+23.5%), from the default configuration.

## Limitations

These constrain how far the results should be read, and are stated here rather
than left for a reader to discover.

1. **The history is 60 days.** That is roughly eight weekly cycles. It is enough
   to establish a day-of-week profile and to run four folds, but it is a short
   series, and the default backtest scores 28 observations per model. The
   improvement figures are stable across configurations, but they are measured
   on one dataset, not validated across many.

2. **The `orders` series is near-constant in the sample data** (79 to 80 orders
   per day, standard deviation 0.13). Every model scores essentially zero error,
   so the comparison carries no information and no improvement figure is quoted
   for it. This is a limitation of the sample generator, which emits a fixed
   daily order volume, not a forecasting result. Adding realistic daily
   variation to order volume would make this a meaningful target.

3. **`seasonal_naive_7` performs worse than `naive` on `net_sales`** (−6.6%)
   while beating it on `units` (+11.0%). Repeating a single recent week carries
   that week's noise into the forecast; averaging weekdays across the full
   history does not. The two seasonal models capture the same structure with
   very different variance, and only the averaged one is reliably better.

4. **Accuracy is measured at total-company daily grain.** Segment-level
   forecasts have not been evaluated and should not be assumed to reach the same
   accuracy; thinner series are noisier.

5. **These are baseline methods.** No trend, holiday, or promotion effects are
   modelled, because the sample data contains none that would justify them.

## Relationship to other layers

The forecast is one of three comparison bases for variance analysis, alongside
prior period and target. It supplies the expected value; variance analysis
computes the gap; driver decomposition attributes that gap to segments. The
forecast never asserts a cause.
