-- Segments where fulfilment fell while demand did not: a supply watchlist.
--
-- This is the fulfilment-constraint pattern from the RootSignal engine
-- expressed in SQL. It reports where the evidence points, deliberately using
-- neutral language: a row here is a segment worth investigating, not a proven
-- supply failure.
--
-- Demand holding up is the discriminator. A segment fulfilling less of a
-- shrinking order book is a different problem from one that cannot keep up with
-- a growing one, and only the second is a supply story.
WITH weekly AS (
    SELECT
        DATE(m.date, 'weekday 0', '-6 days') AS week_start,
        m.region_code,
        m.category,
        SUM(m.ordered_units)                 AS ordered_units,
        SUM(m.fulfilled_units)               AS fulfilled_units,
        ROUND(SUM(m.net_sales), 2)           AS net_sales,
        COUNT(DISTINCT m.date)               AS days_observed
    FROM mart_commercial_daily AS m
    GROUP BY week_start, m.region_code, m.category
),
stock AS (
    -- Stock is a level and is averaged; stockout_flag is a rate over SKU-days.
    -- Summing a level would report a week's stock as seven times its own size.
    SELECT
        DATE(i.date, 'weekday 0', '-6 days') AS week_start,
        i.region_code,
        i.category,
        ROUND(AVG(i.available_stock), 2)     AS available_stock,
        ROUND(AVG(i.stockout_flag), 4)       AS stockout_rate
    FROM stg_inventory AS i
    GROUP BY week_start, i.region_code, i.category
),
joined AS (
    SELECT
        w.week_start,
        w.region_code,
        w.category,
        w.days_observed,
        w.ordered_units,
        w.fulfilled_units,
        w.net_sales,
        s.available_stock,
        s.stockout_rate,
        CASE WHEN w.ordered_units <> 0
             THEN CAST(w.fulfilled_units AS REAL) / w.ordered_units END AS fill_rate
    FROM weekly AS w
    LEFT JOIN stock AS s
           ON s.week_start = w.week_start
          AND s.region_code = w.region_code
          AND s.category = w.category
),
movement AS (
    SELECT
        *,
        LAG(fill_rate)       OVER segment AS prior_fill_rate,
        LAG(ordered_units)   OVER segment AS prior_ordered_units,
        LAG(available_stock) OVER segment AS prior_available_stock,
        LAG(stockout_rate)   OVER segment AS prior_stockout_rate
    FROM joined
    WINDOW segment AS (PARTITION BY region_code, category ORDER BY week_start)
)
SELECT
    week_start,
    region_code,
    category,
    ROUND(fill_rate, 4)                              AS fill_rate,
    ROUND(prior_fill_rate, 4)                        AS prior_fill_rate,
    ROUND(fill_rate - prior_fill_rate, 4)            AS fill_rate_change,
    ordered_units,
    prior_ordered_units,
    ROUND(CAST(ordered_units AS REAL) / prior_ordered_units - 1, 4) AS demand_change_pct,
    available_stock,
    ROUND(available_stock / prior_available_stock - 1, 4)           AS stock_change_pct,
    stockout_rate,
    ordered_units - fulfilled_units                                 AS unfulfilled_units,
    days_observed
FROM movement
WHERE prior_fill_rate IS NOT NULL
  -- Fulfilment fell materially...
  AND fill_rate - prior_fill_rate <= -0.05
  -- ...while demand did not, so this is not simply a shrinking segment.
  AND CAST(ordered_units AS REAL) / prior_ordered_units >= 0.95
ORDER BY fill_rate_change;
