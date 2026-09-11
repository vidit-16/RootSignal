-- Sales transaction lines enriched with product, customer, manager and geography.
--
-- Mirrors modeling.consolidation.enrich_sales. Every join is many-to-one on a
-- unique dimension key, so the order-line grain of fact_sales is preserved and
-- no row is multiplied.
DROP VIEW IF EXISTS stg_sales;
CREATE VIEW stg_sales AS
SELECT
    s.order_id,
    s.date,
    s.customer_id,
    s.region_code,
    s.channel,
    s.kam_id,
    s.sku_id,
    s.category,
    s.sales_type,
    s.units,
    s.unit_price,
    s.discount_pct,
    s.net_sales,
    s.units * s.unit_price                         AS gross_sales,
    s.units * s.unit_price - s.net_sales           AS discount_value,
    sku.sku_name,
    sku.sub_category,
    sku.pack_size_kg,
    sku.unit_cost,
    sku.list_price,
    c.customer_name,
    c.customer_type,
    k.kam_name,
    r.city,
    r.zone
FROM fact_sales AS s
JOIN dim_sku      AS sku ON sku.sku_id      = s.sku_id
JOIN dim_customer AS c   ON c.customer_id   = s.customer_id
JOIN dim_kam      AS k   ON k.kam_id        = s.kam_id
JOIN dim_region   AS r   ON r.region_code   = s.region_code;
