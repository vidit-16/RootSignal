-- Weekly fill rate by region and category, against the service-level target.
--
-- Weeks run Monday to Sunday. days_observed is reported so a partial week at
-- either end of the range is visible rather than silently compared against a
-- full one.
--
-- Fill rate is rebuilt from summed units at the weekly grain. Averaging daily
-- fill rates would weight a quiet day the same as a busy one.
WITH weekly AS (
    SELECT
        DATE(date, 'weekday 0', '-6 days') AS week_start,
        region_code,
        category,
        COUNT(DISTINCT date)               AS days_observed,
        SUM(ordered_units)                 AS ordered_units,
        SUM(fulfilled_units)               AS fulfilled_units,
        SUM(cancelled_units)               AS cancelled_units,
        ROUND(SUM(net_sales), 2)           AS net_sales
    FROM mart_commercial_daily
    GROUP BY week_start, region_code, category
),
target AS (
    SELECT
        DATE(date, 'weekday 0', '-6 days') AS week_start,
        region_code,
        category,
        ROUND(AVG(fill_rate_target), 4)    AS fill_rate_target
    FROM fact_targets
    GROUP BY week_start, region_code, category
)
SELECT
    w.week_start,
    w.region_code,
    w.category,
    w.days_observed,
    w.ordered_units,
    w.fulfilled_units,
    CASE WHEN w.ordered_units <> 0
         THEN ROUND(CAST(w.fulfilled_units AS REAL) / w.ordered_units, 4) END AS fill_rate,
    t.fill_rate_target,
    CASE WHEN w.ordered_units <> 0
         THEN ROUND(CAST(w.fulfilled_units AS REAL) / w.ordered_units - t.fill_rate_target, 4)
    END                                                                       AS fill_rate_variance,
    w.ordered_units - w.fulfilled_units                                       AS unfulfilled_units
FROM weekly AS w
LEFT JOIN target AS t
       ON t.week_start = w.week_start
      AND t.region_code = w.region_code
      AND t.category = w.category
ORDER BY w.week_start, w.region_code, w.category;
