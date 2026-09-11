-- Daily inventory snapshots enriched with product attributes.
--
-- Kept separate from the commercial staging views: inventory sits at
-- SKU x warehouse x day, and joining it to order lines would multiply rows.
DROP VIEW IF EXISTS stg_inventory;
CREATE VIEW stg_inventory AS
SELECT
    i.date,
    i.sku_id,
    i.warehouse,
    i.region_code,
    i.opening_stock,
    i.received_units,
    i.available_stock,
    i.ordered_units,
    i.fulfilled_units,
    i.stockout_flag,
    sku.sku_name,
    sku.category,
    sku.sub_category,
    r.city,
    r.zone
FROM fact_inventory AS i
JOIN dim_sku    AS sku ON sku.sku_id    = i.sku_id
JOIN dim_region AS r   ON r.region_code = i.region_code;
