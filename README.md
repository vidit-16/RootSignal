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
| Pipeline contract | End-to-end test from generation through mart reconciliation | [pipeline_contract.md](docs/pipeline_contract.md) |

### Measured results

Numbers below come from the committed test suite and evaluation scripts, not from estimates.

- **Forecast accuracy:** `seasonal_mean_7` reduces WAPE by **23.5%** against a
  naive baseline on daily net sales (rolling-origin backtest, horizon 7,
  4 folds, 28 scored observations). It ranks first in every backtest
  configuration tested; 23.5% is the most conservative of those. Reproduce with
  `python scripts/evaluate_forecasts.py`.
- **Data quality:** validation detects **8 errors** across the 9 raw tables. Cleaning
  imputes 3 recoverable fields, removes 2 exact duplicates, and quarantines
  1 impossible order line, after which validation passes with zero errors.
- **Reconciliation:** the commercial mart reconciles exactly to cleaned facts on
  net sales, units, ordered units, and fulfilled units.
- **Variance coverage:** plan-versus-actual attainment spans **85.1% to 119.0%**
  across 64 region/category/channel segments (23 above plan, 41 below), so target
  variance separates segments instead of failing them uniformly.
- **Scenario detection:** the seeded supply disruption is detected without being
  told where to look. Weekly fill rate in the affected Bengaluru segments falls
  from 0.99 to 0.69 and crosses from above target to **0.227 below** it.
- **Tests:** 80 automated tests covering validation, cleaning, KPIs, consolidation,
  SQL schema conformance, forecasting, trend and variance analysis, and the
  end-to-end pipeline.

### Not yet built

Driver decomposition, root-cause signals, impact estimation, confidence scoring,
the optional LLM explanation layer, the Streamlit dashboard, Excel reporting,
SQL marts and analytics queries, database ingestion, and Docker.
