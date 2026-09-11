-- Customer order lines enriched with product, customer, manager and geography.
--
-- Mirrors modeling.consolidation.enrich_orders. kam_id comes from the customer
-- master rather than from the order line, because a key account manager owns
-- customers rather than orders.
DROP VIEW IF EXISTS stg_orders;
CREATE VIEW stg_orders AS
SELECT
    o.order_id,
    o.date,
    o.customer_id,
    o.region_code,
    o.channel,
    o.sku_id,
    o.ordered_units,
    o.fulfilled_units,
    o.cancelled_units,
    o.order_status,
    o.sales_type,
    sku.sku_name,
    sku.category,
    sku.sub_category,
    sku.pack_size_kg,
    c.customer_name,
    c.customer_type,
    c.kam_id,
    k.kam_name,
    r.city,
    r.zone
FROM fact_orders  AS o
JOIN dim_sku      AS sku ON sku.sku_id    = o.sku_id
JOIN dim_customer AS c   ON c.customer_id = o.customer_id
JOIN dim_kam      AS k   ON k.kam_id      = c.kam_id
JOIN dim_region   AS r   ON r.region_code = o.region_code;
