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

### Why seasonal methods are justified, and where they help most

Not by habit, but by the data — and the data says something more specific than
"there is weekly seasonality".

| Autocorrelation | `net_sales` | `orders` |
| --- | --- | --- |
| lag-1 | −0.082 | +0.079 |
| lag-7 | +0.056 | **+0.273** |
| lag-14 | **+0.396** | **+0.555** |

Weekday demand shows up mainly as **more orders**, not much larger baskets, so
order volume carries the clearest weekly signal. Net sales — order count times
basket size times price variation, across baskets of one to three lines — is a
noisier composite in which that signal is diluted at lag-7 while surviving at
lag-14.

Lag-1 is near zero for both, which is why carrying the last value forward is a
weak baseline throughout.

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
| **seasonal_mean_7** | 5,183.04 | 6,279.05 | **0.0764** | −2,305.21 | **+27.1%** |
| moving_average_7 | 5,628.26 | 6,758.65 | 0.0829 | −690.11 | +20.8% |
| ses | 5,816.69 | 6,889.42 | 0.0857 | −1,656.21 | +18.1% |
| naive | 7,105.52 | 8,807.80 | 0.1047 | −4,316.16 | baseline |
| seasonal_naive_7 | 8,073.79 | 10,119.36 | 0.1189 | −690.11 | −13.6% |

### `units`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| **seasonal_mean_7** | 25.60 | 32.13 | **0.0814** | +12.50 | **+11.4%** |
| naive | 28.89 | 37.85 | 0.0918 | −9.11 | baseline |
| ses | 29.13 | 35.23 | 0.0926 | +8.70 | −0.8% |
| moving_average_7 | 29.76 | 35.92 | 0.0946 | +7.75 | −3.0% |
| seasonal_naive_7 | 40.39 | 48.20 | 0.1284 | +7.75 | −39.8% |

### `orders`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| **ses** | 4.17 | 5.45 | **0.0523** | +0.94 | **+22.2%** |
| moving_average_7 | 4.37 | 5.64 | 0.0549 | +0.54 | +18.4% |
| seasonal_mean_7 | 4.88 | 5.81 | 0.0613 | +1.59 | +8.9% |
| naive | 5.36 | 7.47 | 0.0672 | −2.93 | baseline |
| seasonal_naive_7 | 5.82 | 7.14 | 0.0731 | +0.54 | −8.7% |

### No single model wins everywhere

`seasonal_mean_7` leads clearly on `net_sales` and `units`. On `orders` the
level-tracking `ses` is ahead, which is worth stating rather than smoothing
over: order volume is a noisier daily count once orders carry several lines, and
tracking its level beats reconstructing a weekday profile from eight weeks of
history.

Every non-naive model beats the baseline on net sales. `seasonal_naive_7` trails
the baseline on all three metrics: repeating one recent week carries that week's
noise into the forecast, where averaging each weekday across the full history
does not.

### Robustness

`seasonal_mean_7` on `net_sales` ranks **first in all six** backtest
configurations tested:

| horizon | initial train | step | folds | WAPE | vs naive |
| --- | --- | --- | --- | --- | --- |
| 7 | 28 | 7 | 4 | 0.0764 | +27.1% |
| 7 | 28 | 3 | 9 | 0.0749 | +36.8% |
| 7 | 21 | 3 | 11 | 0.0927 | +8.3% |
| 7 | 35 | 3 | 7 | 0.0775 | +43.4% |
| 14 | 28 | 7 | 3 | 0.0788 | +30.1% |
| 14 | 35 | 7 | 2 | 0.0741 | +31.0% |

The improvement spans +8.3% to +43.4% depending on configuration. The headline
figure quoted elsewhere is the default configuration's +27.1%, and the range is
reported here because a single number would hide how much it moves.

## Limitations

These constrain how far the results should be read, and are stated here rather
than left for a reader to discover.

1. **The history is 60 days.** That is roughly eight weekly cycles. It is enough
   to establish a day-of-week profile and to run four folds, but it is a short
   series, and the default backtest scores 28 observations per model. Gaps of a
   few percent between the leading models are not resolvable at this size.

2. **`seasonal_naive_7` trails even the naive baseline on all three metrics.**
   Repeating a single recent week carries that week's noise into the forecast;
   averaging each weekday across the full history does not. The two capture the
   same structure with very different variance.

3. **Accuracy is measured at total-company daily grain.** Segment-level
   forecasts have not been evaluated and should not be assumed to reach the same
   accuracy; thinner series are noisier.

4. **These are baseline methods.** No trend, holiday, or promotion effects are
   modelled, because the sample data contains none that would justify them.

## Relationship to other layers

The forecast is one of three comparison bases for variance analysis, alongside
prior period and target. It supplies the expected value; variance analysis
computes the gap; driver decomposition attributes that gap to segments. The
forecast never asserts a cause.
