-- Daily sales tracker: the standing operational view of how trade is running.
--
-- One row per day with company totals, the prior day for comparison, and the
-- plan. Order counts are taken from the mart's distinct counts, so an order
-- carrying several SKUs is counted once.
WITH daily AS (
    SELECT
        date,
        ROUND(SUM(net_sales), 2)     AS net_sales,
        SUM(sales_units)             AS units,
        SUM(ordered_units)           AS ordered_units,
        SUM(fulfilled_units)         AS fulfilled_units,
        SUM(cancelled_units)         AS cancelled_units
    FROM mart_commercial_daily
    GROUP BY date
),
-- Order counts cannot be summed out of the mart. The mart splits by category,
-- and a basket holding a fruit and a vegetable is one order sitting in two
-- cells: correct within each cell, double-counted the moment they are added.
-- The daily figure is therefore a distinct count taken from the order fact.
daily_orders AS (
    SELECT date, COUNT(DISTINCT order_id) AS orders
    FROM fact_orders
    GROUP BY date
),
plan AS (
    SELECT date, ROUND(SUM(sales_target), 2) AS sales_target
    FROM fact_targets
    GROUP BY date
)
SELECT
    d.date,
    d.net_sales,
    d.units,
    do.orders,
    CASE WHEN d.ordered_units <> 0
         THEN ROUND(CAST(d.fulfilled_units AS REAL) / d.ordered_units, 4) END AS fill_rate,
    p.sales_target,
    ROUND(d.net_sales - p.sales_target, 2)                                    AS target_variance,
    CASE WHEN p.sales_target <> 0
         THEN ROUND((d.net_sales - p.sales_target) / p.sales_target, 4) END   AS target_variance_pct,
    LAG(d.net_sales) OVER (ORDER BY d.date)                                   AS prior_day_net_sales,
    ROUND(d.net_sales - LAG(d.net_sales) OVER (ORDER BY d.date), 2)           AS day_over_day_change
FROM daily AS d
JOIN daily_orders AS do ON do.date = d.date
LEFT JOIN plan AS p ON p.date = d.date
ORDER BY d.date;
