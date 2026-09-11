-- Key account manager scorecard: revenue and fulfilment against quota.
--
-- A manager owns customers, not orders, so their sales and orders are resolved
-- through the customer master. Quotas live in fact_kam_targets at their own
-- grain rather than being forced into the region and category plan.
WITH sales AS (
    SELECT
        kam_id,
        ROUND(SUM(net_sales), 2) AS net_sales,
        COUNT(DISTINCT order_id) AS sales_orders
    FROM stg_sales
    GROUP BY kam_id
),
orders AS (
    SELECT
        kam_id,
        COUNT(DISTINCT order_id) AS orders,
        SUM(ordered_units)       AS ordered_units,
        SUM(fulfilled_units)     AS fulfilled_units
    FROM stg_orders
    GROUP BY kam_id
),
quota AS (
    SELECT
        kam_id,
        ROUND(SUM(sales_target), 2) AS sales_target,
        SUM(order_target)           AS order_target
    FROM fact_kam_targets
    GROUP BY kam_id
)
SELECT
    k.kam_id,
    k.kam_name,
    s.net_sales,
    q.sales_target,
    ROUND(s.net_sales - q.sales_target, 2) AS sales_variance,
    CASE WHEN q.sales_target <> 0
         THEN ROUND(s.net_sales / q.sales_target, 4) END AS sales_attainment,
    o.orders,
    q.order_target,
    CASE WHEN o.ordered_units <> 0
         THEN ROUND(CAST(o.fulfilled_units AS REAL) / o.ordered_units, 4) END AS fill_rate
FROM dim_kam AS k
LEFT JOIN sales  AS s ON s.kam_id = k.kam_id
LEFT JOIN orders AS o ON o.kam_id = k.kam_id
LEFT JOIN quota  AS q ON q.kam_id = k.kam_id
ORDER BY sales_attainment;
