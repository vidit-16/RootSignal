-- Daily commercial mart at date x region x category x channel x sales_type.
--
-- This is the SQL expression of modeling.consolidation.build_commercial_mart,
-- and tests assert the two agree row for row. There is one definition of each
-- metric in this project, not one for Python and another for SQL.
--
-- Sales and orders are aggregated independently and only then joined on the
-- common grain. Joining the facts directly would multiply rows whenever an
-- order carries several SKUs, inflating both order counts and units.
--
-- Ratios are computed from the summed components at this grain. Rolling a
-- finer-grained ratio upward by averaging would weight every row equally
-- regardless of the volume behind it.
DROP VIEW IF EXISTS mart_commercial_daily;
CREATE VIEW mart_commercial_daily AS
WITH sales_agg AS (
    SELECT
        date,
        region_code,
        category,
        channel,
        sales_type,
        SUM(units * unit_price)      AS gross_sales,
        SUM(net_sales)               AS net_sales,
        SUM(units)                   AS sales_units,
        COUNT(DISTINCT order_id)     AS sales_order_count
    FROM fact_sales
    GROUP BY date, region_code, category, channel, sales_type
),
orders_agg AS (
    SELECT
        date,
        region_code,
        category,
        channel,
        sales_type,
        COUNT(DISTINCT order_id)     AS order_count,
        SUM(ordered_units)           AS ordered_units,
        SUM(fulfilled_units)         AS fulfilled_units,
        SUM(cancelled_units)         AS cancelled_units
    FROM stg_orders
    GROUP BY date, region_code, category, channel, sales_type
),
-- A grain key may exist in one fact and not the other: an order placed but
-- never fulfilled produces no sales row at all. Both sides are kept so the
-- mart reconciles back to each fact independently.
grain AS (
    SELECT date, region_code, category, channel, sales_type FROM sales_agg
    UNION
    SELECT date, region_code, category, channel, sales_type FROM orders_agg
),
combined AS (
    SELECT
        g.date,
        g.region_code,
        g.category,
        g.channel,
        g.sales_type,
        COALESCE(s.gross_sales, 0)       AS gross_sales,
        COALESCE(s.net_sales, 0)         AS net_sales,
        COALESCE(s.sales_units, 0)       AS sales_units,
        COALESCE(s.sales_order_count, 0) AS sales_order_count,
        COALESCE(o.order_count, 0)       AS order_count,
        COALESCE(o.ordered_units, 0)     AS ordered_units,
        COALESCE(o.fulfilled_units, 0)   AS fulfilled_units,
        COALESCE(o.cancelled_units, 0)   AS cancelled_units
    FROM grain AS g
    LEFT JOIN sales_agg AS s
           ON s.date = g.date
          AND s.region_code = g.region_code
          AND s.category = g.category
          AND s.channel = g.channel
          AND s.sales_type = g.sales_type
    LEFT JOIN orders_agg AS o
           ON o.date = g.date
          AND o.region_code = g.region_code
          AND o.category = g.category
          AND o.channel = g.channel
          AND o.sales_type = g.sales_type
)
SELECT
    date,
    region_code,
    category,
    channel,
    sales_type,
    ROUND(gross_sales, 2)                                   AS gross_sales,
    ROUND(net_sales, 2)                                     AS net_sales,
    sales_units,
    sales_order_count,
    order_count,
    ordered_units,
    fulfilled_units,
    cancelled_units,
    -- Zero demand leaves fill rate undefined rather than forcing it to zero:
    -- a period nobody ordered in was not a period nobody fulfilled.
    CASE WHEN ordered_units <> 0
         THEN ROUND(CAST(fulfilled_units AS REAL) / ordered_units, 4) END AS fill_rate,
    CASE WHEN ordered_units <> 0
         THEN ROUND(CAST(cancelled_units AS REAL) / ordered_units, 4) END AS cancellation_rate,
    CASE WHEN sales_order_count <> 0
         THEN ROUND(net_sales / sales_order_count, 2) END               AS aov,
    ROUND(gross_sales - net_sales, 2)                                   AS discount_value
FROM combined;

-- Note on aov: SQLite's ROUND breaks exact halves away from zero while NumPy
-- breaks them to even, so aov can differ from the Python mart by one cent on
-- values landing exactly on a half-cent (about 5% of rows here, in both
-- directions). The calculation is identical; only the tie-break convention
-- differs. Tests assert every other column matches exactly and that aov agrees
-- to within a cent.
