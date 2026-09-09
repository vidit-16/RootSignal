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

The project is being built incrementally, starting with the data model, sample business dataset, ingestion, validation, and analytical foundation.
