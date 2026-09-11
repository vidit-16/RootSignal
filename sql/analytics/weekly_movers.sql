-- Week-over-week net sales movement by region and category, with each
-- segment's share of the total move.
--
-- Contributions are computed before anything is ordered. Sorting by sales level
-- would surface the largest segments, which is a different question: a big
-- segment that barely moved explains nothing.
--
-- Shares are of the net movement, so they can exceed 100% when segments offset
-- one another. That is arithmetically correct and worth reading carefully.
WITH weekly AS (
    SELECT
        DATE(date, 'weekday 0', '-6 days') AS week_start,
        region_code,
        category,
        ROUND(SUM(net_sales), 2)           AS net_sales,
        COUNT(DISTINCT date)               AS days_observed
    FROM mart_commercial_daily
    GROUP BY week_start, region_code, category
),
movement AS (
    SELECT
        week_start,
        region_code,
        category,
        net_sales,
        days_observed,
        LAG(net_sales) OVER (PARTITION BY region_code, category ORDER BY week_start) AS prior_net_sales
    FROM weekly
),
contribution AS (
    SELECT
        week_start,
        region_code,
        category,
        net_sales,
        prior_net_sales,
        days_observed,
        ROUND(net_sales - prior_net_sales, 2) AS contribution
    FROM movement
    WHERE prior_net_sales IS NOT NULL
)
SELECT
    c.week_start,
    c.region_code,
    c.category,
    c.prior_net_sales,
    c.net_sales,
    c.contribution,
    ROUND(c.contribution / NULLIF(SUM(c.contribution) OVER (PARTITION BY c.week_start), 0), 4)
        AS share_of_net_movement,
    ROUND(ABS(c.contribution) / SUM(ABS(c.contribution)) OVER (PARTITION BY c.week_start), 4)
        AS share_of_absolute_movement,
    c.days_observed
FROM contribution AS c
ORDER BY c.week_start, c.contribution;
