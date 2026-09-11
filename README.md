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
| Sample-data generator | Deterministic 60-day dataset (seed 42) with multi-SKU baskets, controlled quality defects, and four labelled scenarios | — |
| Ingestion | CSV, Excel and database loading by table name | — |
| Validation | Columns, keys, ranges, referential integrity, cross-table reconciliation | — |
| Cleaning | Quarantine-first repair with a full audit trail | [cleaning.md](docs/cleaning.md) |
| Consolidation | Grain-safe enrichment and the commercial mart | [consolidation.md](docs/consolidation.md) |
| KPI engine | Deterministic sales, order, fill-rate, mix, growth, and variance metrics | [kpis.md](docs/kpis.md) |
| Forecasting | Five deterministic daily models with rolling-origin backtesting | [forecasting.md](docs/forecasting.md) |
| Trend and variance | Day/week/month movement, and variance against prior period, target, and forecast under one schema | [analysis.md](docs/analysis.md) |
| Driver decomposition | Exact attribution of a movement to segments, with rate/mix separation for ratios | [decomposition.md](docs/decomposition.md) |
| Impact estimation | Fulfilment shortfall valued at realised prices, with every assumption stated | [signals.md](docs/signals.md) |
| RootSignal engine | Evidence, pattern, impact, confidence and recommended investigation, ranked | [signals.md](docs/signals.md) |
| Scenario evaluation | Measures whether the engine tells four planted situations apart | [signals.md](docs/signals.md) |
| SQL layer | Staging views, commercial mart and seven business queries, all executed by tests | [sql.md](docs/sql.md) |
| Excel reporting | Six operational workbooks, each opening with what its figures mean | [reporting.md](docs/reporting.md) |
| Pipeline contract | End-to-end test from generation through mart reconciliation | [pipeline_contract.md](docs/pipeline_contract.md) |

### Measured results

Numbers below come from the committed test suite and evaluation scripts, not from estimates.

- **Scenario detection:** the dataset carries four deliberately different
  situations — a supply constraint, a demand decline, a mix shift, and a region
  where nothing happens. The engine **correctly classifies all three planted
  situations** at a medium confidence floor, or **two of three with zero false
  alarms and complete silence on the control** at a high floor. Ground truth
  travels with the data; reproduce with `python scripts/evaluate_signals.py`.
- **End-to-end signal quality:** given only a fill-rate movement across every
  region and category, with nothing naming Bengaluru, Fruits or supply, the top
  signal is **BLR Fruits** — a fulfilment constraint at high confidence, an
  estimated impact of **8,325**, and a concrete recommended investigation. It
  scores 5 of 6 criteria rather than 6, because demand also softened and the
  engine refuses to dismiss that alternative.
- **Forecast accuracy:** `seasonal_mean_7` reduces WAPE against a naive baseline
  by **27.1%** on daily net sales and **11.4%** on units, ranking first in all six
  backtest configurations tested (range +8.3% to +43.4%). On order volume the
  level-tracking `ses` leads at **+22.2%**. Reproduce with
  `python scripts/evaluate_forecasts.py`.
- **SQL/Python parity:** the commercial mart is implemented twice, in Python and
  in SQL, and a test asserts the two agree **row for row across 3,663 rows and
  eleven metric columns**. Separately, the SQL supply watchlist and the Python
  signal engine — which share no code — independently return the same two
  disrupted segments.
- **Data quality:** validation detects **8 errors** across the 10 raw tables. Cleaning
  imputes 3 recoverable fields, removes 2 exact duplicates, and quarantines
  1 impossible order line, after which validation passes with zero errors.
- **Reconciliation:** the commercial mart reconciles exactly to cleaned facts on
  net sales, units, ordered units, and fulfilled units.
- **Variance coverage:** plan-versus-actual attainment spans **82.1% to 115.6%**
  across 64 region/category/channel segments (27 above plan, 37 below). KAM quota
  attainment runs 93.0% to 110.0% across the four key account managers.
- **Tests:** 180 automated tests covering validation, cleaning, KPIs, consolidation,
  SQL schema conformance and parity, forecasting, trend and variance analysis,
  driver decomposition, impact estimation, signal assembly, scenario evaluation,
  and Excel reporting. One asserts that no signal output ever claims causation.

### Not yet built

The optional LLM explanation layer, the Streamlit dashboard, and Docker.
