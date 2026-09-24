# Power BI on the RootSignal warehouse

The pipeline loads the cleaned model and the analysis results into PostgreSQL.
This report reads them from there and computes nothing the project has not
already computed and tested: revenue comes from the fact table, and the country
contributions, the rate and mix split, month-on-month change and forecast
accuracy come from report tables the pipeline writes.

About 30 minutes in Power BI Desktop (Windows, free).

## 1. Fill the warehouse

```bash
docker compose up -d warehouse
docker compose run --rm pipeline
```

The second command takes about eight minutes and ends with a list of loaded
tables. If the warehouse was filled before, only the first command is needed.

## 2. Connect

1. Open Power BI Desktop and close the start screen.
2. **Home** → **Get data** → **More…** → **Database** → **PostgreSQL database**
   → **Connect**.
3. Server `localhost:5433`, database `rootsignal`, mode **Import** → **OK**.
4. In the credentials window choose **Database** on the left. User name
   `rootsignal`, password `rootsignal` → **Connect**. These belong to the local
   container, set in `docker-compose.yml`.
5. Power BI says it could not connect with encryption and asks to connect
   without it. The local container has no certificate: **OK**.
6. In the Navigator, expand `rootsignal` and tick:

   | Table | What it holds |
   | --- | --- |
   | `rootsignal.fact_sales` | one row per invoice line, after cleaning (996,531) |
   | `rootsignal.dim_date` | one row per trading day |
   | `rootsignal.dim_region` | one row per country |
   | `rootsignal.dim_sku` | one row per product |
   | `rootsignal.dim_customer` | one row per customer |
   | `rootsignal.rpt_monthly_trade` | revenue, units, invoices and AOV per month |
   | `rootsignal.rpt_monthly_trend` | each month against the one before |
   | `rootsignal.rpt_country_contribution` | how much each country moved the latest month's revenue |
   | `rootsignal.rpt_return_rate_components` | the return rate's movement split into rate, mix and interaction |
   | `rootsignal.rpt_return_rate_split` | that split, country by country |
   | `rootsignal.rpt_forecast_accuracy` | backtested forecast error per model against the naive baseline |

7. **Load**, not Transform. Loading a million rows takes a minute.

## 3. Model

1. Open **Model view** (third icon on the left).
2. Power BI may have drawn some relationships already. Open **Home** →
   **Manage relationships** and make sure exactly these four exist, each
   **Many to one (\*:1)** with cross-filter direction **Single**. Create any that
   are missing with **New…**, and delete any others:

   | From (many) | To (one) |
   | --- | --- |
   | `fact_sales` `date` | `dim_date` `date` |
   | `fact_sales` `sku_id` | `dim_sku` `sku_id` |
   | `fact_sales` `customer_id` | `dim_customer` `customer_id` |
   | `fact_sales` `region_code` | `dim_region` `region_code` |

   The `rpt_` tables stay unconnected. They are finished results, not facts to
   slice.
3. Hide the key columns of `fact_sales` from report view so they are not
   dragged onto visuals by mistake: right-click `order_id`, `sku_id`,
   `customer_id` → **Hide in report view**.

## 4. Measures

Select `fact_sales` in the **Data** pane on the right, then for each measure:
**Home** → **New measure**, paste it, press Enter.

```DAX
Net Sales = SUM ( fact_sales[net_sales] )
```

```DAX
Units = SUM ( fact_sales[units] )
```

```DAX
Invoices = DISTINCTCOUNT ( fact_sales[order_id] )
```

```DAX
AOV = DIVIDE ( [Net Sales], [Invoices] )
```

`Invoices` is a distinct count on purpose: one invoice lists several products,
so adding up per-product counts would count it several times.

Format them: select `Net Sales`, then **Measure tools** → **Format**: Currency,
£ English (United Kingdom), 0 decimals. Do the same for `AOV` with 2 decimals.
`Units` and `Invoices`: Whole number, thousands separator on.

Month-on-month change is not a measure here. `dim_date` holds only days the
retailer traded, and Power BI's time-intelligence functions assume an unbroken
calendar, so `DATEADD` would quietly miscount months with different trading
days. The project already computes the change in `rpt_monthly_trend`, and page 1
uses that.

## 5. Page 1: Overview

Rename the page (double-click the tab) to **Overview**.

1. **Four cards.** From **Visualizations**, add a **Card** and drop `Net Sales`
   into **Fields**. Repeat for `Invoices`, `AOV` and `Units`. Line them up
   across the top.
2. **Revenue by month.** Add a **Line chart**. **X-axis**:
   `rpt_monthly_trade` `period_start`; **Y-axis**: `rpt_monthly_trade`
   `net_sales`. On the X-axis field choose `period_start` itself, not
   **Date Hierarchy** (click the arrow next to it).
   In the **Filters** pane, under **Filters on this visual**, set `is_complete`
   to **True** only. The data stops on 9 December 2011, and a nine-day December
   would otherwise read as a collapse.
3. **Month-on-month table.** Add a **Table** with `rpt_monthly_trend`
   `period_start`, `value`, `previous_value` and `pct_change`. Filter it to
   `metric` = `net_sales`. Format `pct_change` as a percentage.
4. **Country slicer.** Add a **Slicer** with `dim_region` `region_code`. It
   filters the four cards; the chart and the table show the whole business.

## 6. Page 2: What moved revenue

New page (**+** at the bottom), named **Drivers**.

1. Add a **Clustered bar chart**. **Y-axis**: `rpt_country_contribution`
   `segment`; **X-axis**: `rpt_country_contribution` `contribution`.
2. Sort by contribution: **…** on the visual → **Sort axis** → `contribution`.
3. Add a **Card** with `rpt_country_contribution` `period_start`
   (**Earliest**) and a second with `comparison_period` (**Earliest**), so the
   page says which months are compared.
4. Title the chart *Change in net sales by country*.

Most of the movement comes from the United Kingdom, which carries most of the
revenue. The short bars show the markets that moved the other way.

## 7. Page 3: Why the return rate moved

New page, named **Returns**.

1. Add a **Clustered column chart**. **X-axis**:
   `rpt_return_rate_components` `component`; **Y-axis**: `net`
   (**Don't summarize**, or Sum; there is one row per component).
2. Add a **Table** with `rpt_return_rate_split` `segment`, `rate_before`,
   `rate_after`, `rate_effect`, `mix_effect`. Format the rates as percentages.
3. Title the chart *Return rate movement: rate against mix*.

`rate_effect` is countries genuinely returning a different share of what they
buy. `mix_effect` is demand moving toward countries that always returned more.
Here the rate effect carries almost all of the movement.

A return rate is not a fill rate: returns are customers sending goods back, not
a warehouse failing to ship.

## 8. Page 4: Forecast accuracy

New page, named **Forecast**.

1. Add a **Clustered bar chart**. **Y-axis**: `rpt_forecast_accuracy` `model`;
   **X-axis**: `wape` (Sum). Sort ascending by `wape`: lower is better.
2. Add a **Table** with `model`, `wape`, `wape_improvement_vs_baseline` and
   `folds`. Format `wape` and `wape_improvement_vs_baseline` as percentages.
3. Title the chart *Forecast error by model (lower is better)*.

`seasonal_mean_7` should show about 38.8% lower error than `naive`, the same
figure the README reports.

## 9. Save and share

1. **File** → **Save as** → `RootSignal.pbix`, anywhere outside the repository.
   With a million rows imported it is tens of megabytes, which is why the
   repository keeps screenshots rather than the file.
2. For each page, **File** → **Export** → **Export to PDF**, or take a
   screenshot of the page at full width. Save them as
   `docs/screenshots/powerbi_overview.png`, `powerbi_drivers.png`,
   `powerbi_returns.png` and `powerbi_forecast.png`.

## If something does not match

- **Invoices looks too high:** it is `Count` instead of `Count (Distinct)`, or
  the measure was built on a different column than `order_id`.
- **The line drops sharply at the end:** the `is_complete` filter is missing.
- **A card does not change with the slicer:** it uses an `rpt_` column instead
  of a measure. The `rpt_` tables are whole-business results and do not respond
  to the slicer.
- **The navigator shows no tables:** the pipeline has not run, or the warehouse
  container is stopped (`docker compose up -d warehouse`).
