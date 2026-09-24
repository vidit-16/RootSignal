# Power BI on the RootSignal warehouse

The pipeline loads the cleaned model and the analysis results into PostgreSQL.
This report reads them from there. It computes nothing that the project has
not already computed and tested: revenue comes from the fact table, and the
country contributions, the rate and mix split and the forecast accuracy come
from report tables that `scripts/run_pipeline.py` writes.

## Connect

1. Start the warehouse and fill it:
   ```bash
   docker compose up -d warehouse
   docker compose run --rm pipeline
   ```
2. In Power BI Desktop: **Get data** → **PostgreSQL database**.
   - Server: `localhost:5433`
   - Database: `rootsignal`
   - Data connectivity mode: **Import**
3. Credentials: **Database**, user `rootsignal`, password `rootsignal`. These
   are the local container's, set in `docker-compose.yml`.
4. The local container has no TLS certificate. Power BI asks whether to connect
   without encryption; for this local warehouse, **OK**.
5. Tick these, all in the `rootsignal` schema:

   | Table | What it is |
   | --- | --- |
   | `fact_sales` | one row per invoice line, after cleaning |
   | `dim_date`, `dim_sku`, `dim_customer`, `dim_region` | the dimensions |
   | `rpt_monthly_trade` | revenue, units, invoices and AOV per month |
   | `rpt_country_contribution` | how much each country moved the latest month's revenue |
   | `rpt_return_rate_components` | the return rate's movement split into rate, mix and interaction |
   | `rpt_return_rate_split` | the same split, country by country |
   | `rpt_returns_monthly` | units sold and returned per country and month |
   | `rpt_forecast_accuracy` | backtested forecast error for each model against the naive baseline |

## Model

In **Model view**, relate the fact to its dimensions, many to one, single
direction:

| From | To |
| --- | --- |
| `fact_sales[date]` | `dim_date[date]` |
| `fact_sales[sku_id]` | `dim_sku[sku_id]` |
| `fact_sales[customer_id]` | `dim_customer[customer_id]` |
| `fact_sales[region_code]` | `dim_region[region_code]` |

Mark `dim_date` as the date table (**Table tools** → **Mark as date table**,
column `date`). The `rpt_` tables stand alone and need no relationships.

## Measures

Add these to `fact_sales`:

```DAX
Net Sales = SUM ( fact_sales[net_sales] )

Units = SUM ( fact_sales[units] )

Invoices = DISTINCTCOUNT ( fact_sales[order_id] )

AOV = DIVIDE ( [Net Sales], [Invoices] )

Net Sales Previous Month =
    CALCULATE ( [Net Sales], DATEADD ( dim_date[date], -1, MONTH ) )

Net Sales MoM % =
    DIVIDE ( [Net Sales] - [Net Sales Previous Month], [Net Sales Previous Month] )
```

`Invoices` is a distinct count on purpose. An invoice lists several products,
so summing invoice counts across products counts one invoice several times;
the SQL mart makes the same point.

## Pages

**Overview.** Cards for Net Sales, Invoices, AOV and Net Sales MoM %. A line
chart of Net Sales by `dim_date[date]` at month level. A slicer on
`dim_region[region_code]`.

**What moved revenue.** A bar chart of `rpt_country_contribution`, axis
`segment`, value `contribution`, sorted by value. The United Kingdom accounts
for most of the movement, and the smaller bars show which other markets pulled
the other way.

**Why the return rate moved.** A bar chart of `rpt_return_rate_components`,
axis `component`, value `net`. Most of the movement is `rate_effect`: countries
genuinely returning a different share, rather than demand shifting toward
countries that always return more (`mix_effect`). Beside it, a table of
`rpt_return_rate_split` with `segment`, `rate_before`, `rate_after`,
`rate_effect` and `mix_effect`.

**Forecast accuracy.** A bar chart of `rpt_forecast_accuracy`, axis `model`,
value `wape`, with `wape_improvement_vs_baseline` as a tooltip. Lower WAPE is
better; `naive` is the baseline every model is measured against.

## Things to keep in mind

- `rpt_monthly_trade` and `rpt_returns_monthly` include an `is_complete` flag.
  The dataset stops on 9 December 2011, so the last month is partial: filter on
  `is_complete = TRUE` before comparing months, or December 2011 reads as a
  collapse.
- Order, fulfilment, inventory and target columns are empty for this dataset.
  It records sales only, and the project reports those analyses as unavailable
  rather than approximating them.
- A return rate is not a fill rate. Returns are customers sending goods back,
  not a warehouse failing to ship.
