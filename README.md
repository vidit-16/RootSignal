# RootSignal

Evidence-backed analytics for sales, supply, and business performance.

RootSignal is an analytics system designed to answer a practical business question: **a metric changed, so what changed, where did it happen, how large is the impact, and what should be investigated next?**

## Project goals

- Ingest CSV, Excel, and database-backed business data
- Clean, validate, reconcile, and consolidate operational datasets
- Track sales, orders, fulfillment, fill rate, inventory, customers, KAMs, SKUs, and targets
- Compare actuals against targets, prior periods, and forecasts
- Separate primary and secondary sales
- Detect trends, gaps, and variances
- Trace metric movement across business dimensions
- Estimate business impact and prioritize investigation areas
- Generate evidence-backed explanations
- Produce dashboard views and Excel-ready reports

## Architecture

```text
CSV / Excel / DB
       |
       v
Ingestion -> Validation -> Cleaning -> Consolidation
       |
       v
Business Data Model
       |
       +--> KPI Engine
       +--> Trend / Variance Analysis
       +--> Forecasting
       +--> Driver Decomposition
       +--> Impact Estimation
       |
       v
Evidence Package
       |
       +--> Dashboard / Reporting
       +--> Optional LLM Explanation
       |
       v
Recommended Investigation
```

## Planned stack

Python, Pandas, NumPy, SQL, SQLite/PostgreSQL, Streamlit, Plotly/Matplotlib, scikit-learn or statsmodels where useful, pytest, Docker, GitHub Actions, and an optional OpenAI-compatible LLM API.

Power BI is intentionally outside the core project scope.

## Status

### Built and tested

| Layer | What it does | Docs |
| --- | --- | --- |
| Data model + SQL schema | Dimensions and facts with grains and composite keys enforced in both SQL and Python | [data_model.md](docs/data_model.md) |
| Sample-data generator | Deterministic 60-day dataset (seed 42) with controlled quality defects | — |
| Ingestion | CSV and Excel loading by table name | — |
| Validation | Columns, keys, ranges, referential integrity, cross-table reconciliation | — |
| Cleaning | Quarantine-first repair with a full audit trail | [cleaning.md](docs/cleaning.md) |
| Consolidation | Grain-safe enrichment and the commercial mart | [consolidation.md](docs/consolidation.md) |
| KPI engine | Deterministic sales, order, fill-rate, mix, growth, and variance metrics | [kpis.md](docs/kpis.md) |
| Forecasting | Five deterministic daily models with rolling-origin backtesting | [forecasting.md](docs/forecasting.md) |
| Trend and variance | Day/week/month movement, and variance against prior period, target, and forecast under one schema | [analysis.md](docs/analysis.md) |
| Driver decomposition | Exact attribution of a movement to segments, with rate/mix separation for ratios | [decomposition.md](docs/decomposition.md) |
| Impact estimation | Fulfilment shortfall valued at realised prices, with every assumption stated | [signals.md](docs/signals.md) |
| RootSignal engine | Evidence, pattern, impact, confidence and recommended investigation, ranked | [signals.md](docs/signals.md) |
| Pipeline contract | End-to-end test from generation through mart reconciliation | [pipeline_contract.md](docs/pipeline_contract.md) |

### Measured results

Numbers below come from the committed test suite and evaluation scripts, not from estimates.

- **Forecast accuracy:** `seasonal_mean_7` reduces WAPE against a naive baseline
  by **32.2%** on daily net sales, **36.0%** on units, and **35.2%** on order
  volume (rolling-origin backtest, horizon 7, 4 folds, 28 scored observations).
  It has the best mean rank across all three metrics and six backtest
  configurations; the most conservative improvement measured in any of them is
  +21.0%. Reproduce with `python scripts/evaluate_forecasts.py`.
- **Data quality:** validation detects **8 errors** across the 10 raw tables. Cleaning
  imputes 3 recoverable fields, removes 2 exact duplicates, and quarantines
  1 impossible order line, after which validation passes with zero errors.
- **Reconciliation:** the commercial mart reconciles exactly to cleaned facts on
  net sales, units, ordered units, and fulfilled units.
- **Variance coverage:** plan-versus-actual attainment spans **85.5% to 119.8%**
  across 64 region/category/channel segments (25 above plan, 39 below), so target
  variance separates segments instead of failing them uniformly. KAM quota
  attainment runs 93.4% to 97.4% across the four key account managers.
- **Scenario detection:** the seeded supply disruption is found without being told
  where to look. Weekly fill rate in the affected Bengaluru segments falls from
  0.96 to 0.67 and crosses from above the service target to **0.198 below** it —
  while order volume in those same segments *rose* 9.5% and ordered units rose
  6.3%. Demand could have fallen and did not, which is what makes this a supply
  signal rather than a demand signal.
- **Attribution accuracy:** driver decomposition locates the seeded supply
  disruption unaided. Given only a fill-rate movement across every region and
  category, it ranks **BLR Fruits and BLR Vegetables first and second** without
  either being named as an input. Contributions reconstruct the observed movement
  exactly, for additive metrics and for rates.
- **End-to-end signal detection:** given only a fill-rate movement across every
  region and category, with nothing naming Bengaluru, Fruits or supply, the engine
  returns **BLR Fruits** as its top signal: a fulfilment constraint at **high
  confidence (6 of 6 criteria)**, an estimated impact of **9,174**, and a concrete
  recommended investigation. A second segment on the same run is classified as
  demand softness instead, so the classifier discriminates rather than labelling
  everything a supply problem. Reproduce with `python scripts/detect_signals.py`.
- **Tests:** 140 automated tests covering validation, cleaning, KPIs, consolidation,
  SQL schema conformance, forecasting, trend and variance analysis, driver
  decomposition, impact estimation, signal assembly, and the end-to-end pipeline.
  One asserts that no signal output ever claims causation.

### Not yet built

The optional LLM explanation layer, the Streamlit dashboard, Excel reporting,
SQL marts and analytics queries, database ingestion, and Docker.
