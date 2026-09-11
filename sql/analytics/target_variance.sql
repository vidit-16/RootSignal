-- Plan versus actual by region, category and channel for the whole range.
--
-- Actuals and plan are each aggregated to the shared grain before being joined,
-- so no fact is joined to another fact and no row is multiplied.
WITH actual AS (
    SELECT
        region_code,
        category,
        channel,
        ROUND(SUM(net_sales), 2) AS net_sales,
        SUM(order_count)         AS orders
    FROM mart_commercial_daily
    GROUP BY region_code, category, channel
),
plan AS (
    SELECT
        region_code,
        category,
        channel,
        ROUND(SUM(sales_target), 2) AS sales_target,
        SUM(order_target)           AS order_target
    FROM fact_targets
    GROUP BY region_code, category, channel
)
SELECT
    p.region_code,
    p.category,
    p.channel,
    COALESCE(a.net_sales, 0)                        AS net_sales,
    p.sales_target,
    ROUND(COALESCE(a.net_sales, 0) - p.sales_target, 2) AS variance,
    CASE WHEN p.sales_target <> 0
         THEN ROUND((COALESCE(a.net_sales, 0) - p.sales_target) / p.sales_target, 4) END AS variance_pct,
    CASE WHEN p.sales_target <> 0
         THEN ROUND(COALESCE(a.net_sales, 0) / p.sales_target, 4) END AS attainment
FROM plan AS p
LEFT JOIN actual AS a
       ON a.region_code = p.region_code
      AND a.category = p.category
      AND a.channel = p.channel
ORDER BY variance_pct;
