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
| lag-1 | −0.060 | +0.046 |
| lag-7 | **+0.108** | **+0.527** |
| lag-14 | −0.126 | **+0.411** |

Weekday demand in this business shows up mainly as **more orders**, not much
larger baskets. Order volume therefore carries a strong weekly signal, while
net sales — order count multiplied by basket size and price variation — is a
noisier composite in which that signal is diluted.

This predicts the measured results rather than being an after-the-fact story:
seasonal models dominate on `orders`, where lag-7 is 0.527, and win only
narrowly on `net_sales`, where it is 0.108. The day-of-week profile in net sales
still spans about 20% (Saturday indexes at 1.12 of the mean, Monday at 0.92), so
a weekly model remains worth having — just not decisively better than a
well-tuned level model.

Lag-1 is near zero or negative for both series, which is why carrying the last
value forward is a weak baseline throughout.

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
| ses | 7,626.72 | 10,015.32 | **0.1190** | −271.60 | **+34.9%** |
| moving_average_7 | 7,673.40 | 10,276.28 | 0.1197 | −207.82 | +34.5% |
| seasonal_mean_7 | 7,934.27 | 9,900.39 | 0.1238 | −590.00 | +32.2% |
| seasonal_naive_7 | 9,893.08 | 12,751.90 | 0.1543 | −207.82 | +15.5% |
| naive | 11,710.62 | 14,171.20 | 0.1827 | +2,886.27 | baseline |

### `units`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| ses | 28.63 | 34.77 | **0.0886** | −1.70 | **+37.1%** |
| moving_average_7 | 28.97 | 34.68 | 0.0897 | +1.14 | +36.3% |
| seasonal_mean_7 | 29.12 | 34.21 | 0.0901 | +0.29 | +36.0% |
| seasonal_naive_7 | 32.79 | 40.90 | 0.1015 | +1.14 | +27.9% |
| naive | 45.50 | 53.58 | 0.1408 | +18.64 | baseline |

### `orders`

| Model | MAE | RMSE | WAPE | Bias | vs naive |
| --- | --- | --- | --- | --- | --- |
| seasonal_mean_7 | 5.92 | 6.91 | **0.0703** | −2.19 | **+35.2%** |
| seasonal_naive_7 | 5.96 | 7.42 | 0.0708 | −1.18 | +34.8% |
| moving_average_7 | 6.88 | 7.82 | 0.0817 | −1.18 | +24.7% |
| ses | 7.01 | 8.02 | 0.0832 | −2.17 | +23.3% |
| naive | 9.14 | 10.98 | 0.1085 | +1.00 | baseline |

### No single model wins everywhere

This is the honest reading of the comparison, and it is worth stating plainly
rather than picking whichever model happens to look best per metric.

`ses` is narrowly ahead on `net_sales` and `units`; `seasonal_mean_7` is clearly
ahead on `orders` and `ses` is the second-worst model there. Across all six
backtest configurations and all three metrics, mean rank (1 = best):

| Model | Mean rank | Mean WAPE | net_sales | orders | units |
| --- | --- | --- | --- | --- | --- |
| **seasonal_mean_7** | **1.89** | **0.093** | 2.17 | 1.33 | 2.17 |
| ses | 2.28 | 0.097 | 1.33 | 4.00 | 1.50 |
| moving_average_7 | 2.72 | 0.099 | 2.50 | 3.00 | 2.67 |
| seasonal_naive_7 | 3.17 | 0.111 | 4.17 | 1.67 | 3.67 |
| naive | 4.94 | 0.142 | 4.83 | 5.00 | 5.00 |

`seasonal_mean_7` is therefore the recommended default: it is the most
consistent across metrics, and its deficit to `ses` on sales and units is
around 4% relative, well inside what 28 observations can distinguish. Choosing
`ses` for revenue forecasting specifically would also be defensible.

### Robustness

`seasonal_mean_7` on `net_sales` across backtest configurations:

| horizon | initial train | step | folds | WAPE | vs naive |
| --- | --- | --- | --- | --- | --- |
| 7 | 28 | 7 | 4 | 0.1238 | +32.2% |
| 7 | 28 | 3 | 9 | 0.1205 | +28.0% |
| 7 | 21 | 3 | 11 | 0.1270 | +32.9% |
| 7 | 35 | 3 | 7 | 0.1241 | +21.0% |
| 14 | 28 | 7 | 3 | 0.1201 | +34.1% |
| 14 | 35 | 7 | 2 | 0.1079 | +47.6% |

Every non-naive model beats the baseline in every configuration. The headline
figure quoted elsewhere is the **most conservative** of these (+21.0%).

## Limitations

These constrain how far the results should be read, and are stated here rather
than left for a reader to discover.

1. **The history is 60 days.** That is roughly eight weekly cycles. It is enough
   to establish a day-of-week profile and to run four folds, but it is a short
   series, and the default backtest scores 28 observations per model. Gaps of a
   few percent between the leading models are not resolvable at this size.

2. **`seasonal_naive_7` trails `seasonal_mean_7` on every metric.** Repeating a
   single recent week carries that week's noise into the forecast; averaging
   each weekday across the full history does not. The two capture the same
   structure with very different variance.

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
