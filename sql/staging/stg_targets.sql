-- Commercial plan and KAM quotas, typed and ready to compare against actuals.
DROP VIEW IF EXISTS stg_targets;
CREATE VIEW stg_targets AS
SELECT
    t.date,
    t.region_code,
    t.category,
    t.channel,
    t.sales_target,
    t.order_target,
    t.fill_rate_target,
    r.city,
    r.zone
FROM fact_targets AS t
JOIN dim_region AS r ON r.region_code = t.region_code;

DROP VIEW IF EXISTS stg_kam_targets;
CREATE VIEW stg_kam_targets AS
SELECT
    kt.date,
    kt.kam_id,
    k.kam_name,
    kt.sales_target,
    kt.order_target
FROM fact_kam_targets AS kt
JOIN dim_kam AS k ON k.kam_id = kt.kam_id;
