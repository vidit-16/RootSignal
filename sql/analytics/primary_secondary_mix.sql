-- Primary versus secondary sales split by region and month.
--
-- The two sales types share a fact structure but answer different questions:
-- primary is sell-in to the trade, secondary is sell-through from it. Keeping
-- them separately queryable is why sales_type is part of the mart grain.
WITH monthly AS (
    SELECT
        STRFTIME('%Y-%m', date)                                            AS month,
        region_code,
        ROUND(SUM(CASE WHEN sales_type = 'PRIMARY'   THEN net_sales END), 2) AS primary_sales,
        ROUND(SUM(CASE WHEN sales_type = 'SECONDARY' THEN net_sales END), 2) AS secondary_sales,
        ROUND(SUM(net_sales), 2)                                             AS total_sales
    FROM mart_commercial_daily
    GROUP BY month, region_code
)
SELECT
    month,
    region_code,
    COALESCE(primary_sales, 0)   AS primary_sales,
    COALESCE(secondary_sales, 0) AS secondary_sales,
    total_sales,
    CASE WHEN total_sales <> 0
         THEN ROUND(COALESCE(primary_sales, 0) / total_sales, 4) END   AS primary_mix,
    CASE WHEN total_sales <> 0
         THEN ROUND(COALESCE(secondary_sales, 0) / total_sales, 4) END AS secondary_mix
FROM monthly
ORDER BY month, region_code;
