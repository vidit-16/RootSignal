-- RootSignal analytical schema
-- SQLite-compatible core types; PostgreSQL migration can tighten identity/date types later.

PRAGMA foreign_keys = ON;

CREATE TABLE dim_date (
    date DATE PRIMARY KEY,
    week INTEGER NOT NULL,
    month INTEGER NOT NULL,
    quarter TEXT NOT NULL,
    year INTEGER NOT NULL
);

CREATE TABLE dim_kam (
    kam_id TEXT PRIMARY KEY,
    kam_name TEXT NOT NULL
);

CREATE TABLE dim_region (
    region_code TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    zone TEXT NOT NULL
);

CREATE TABLE dim_sku (
    sku_id TEXT PRIMARY KEY,
    sku_name TEXT NOT NULL,
    category TEXT NOT NULL,
    sub_category TEXT NOT NULL,
    pack_size_kg REAL NOT NULL,
    unit_cost REAL NOT NULL,
    list_price REAL NOT NULL,
    active_flag INTEGER NOT NULL CHECK (active_flag IN (0, 1))
);

CREATE TABLE dim_customer (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    region_code TEXT NOT NULL,
    city TEXT NOT NULL,
    zone TEXT NOT NULL,
    kam_id TEXT NOT NULL,
    FOREIGN KEY (region_code) REFERENCES dim_region(region_code),
    FOREIGN KEY (kam_id) REFERENCES dim_kam(kam_id)
);

-- Grain: one commercial sales transaction line.
-- A single order may carry several SKUs, so order_id alone is not unique;
-- the key must match validation.contracts.TABLE_CONTRACTS['fact_sales'].key_columns.
CREATE TABLE fact_sales (
    order_id TEXT NOT NULL,
    date DATE NOT NULL,
    customer_id TEXT NOT NULL,
    region_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    kam_id TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    category TEXT NOT NULL,
    sales_type TEXT NOT NULL CHECK (sales_type IN ('PRIMARY', 'SECONDARY')),
    units INTEGER NOT NULL CHECK (units >= 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    discount_pct REAL NOT NULL CHECK (discount_pct >= 0 AND discount_pct <= 1),
    net_sales REAL NOT NULL CHECK (net_sales >= 0),
    PRIMARY KEY (order_id, sku_id, sales_type),
    FOREIGN KEY (date) REFERENCES dim_date(date),
    FOREIGN KEY (customer_id) REFERENCES dim_customer(customer_id),
    FOREIGN KEY (region_code) REFERENCES dim_region(region_code),
    FOREIGN KEY (kam_id) REFERENCES dim_kam(kam_id),
    FOREIGN KEY (sku_id) REFERENCES dim_sku(sku_id)
);

-- Grain: one customer order line.
-- Shares the sales key so order counts never multiply across SKUs;
-- must match validation.contracts.TABLE_CONTRACTS['fact_orders'].key_columns.
CREATE TABLE fact_orders (
    order_id TEXT NOT NULL,
    date DATE NOT NULL,
    customer_id TEXT NOT NULL,
    region_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    ordered_units INTEGER NOT NULL CHECK (ordered_units >= 0),
    fulfilled_units INTEGER NOT NULL CHECK (fulfilled_units >= 0),
    cancelled_units INTEGER NOT NULL CHECK (cancelled_units >= 0),
    order_status TEXT NOT NULL,
    sales_type TEXT NOT NULL CHECK (sales_type IN ('PRIMARY', 'SECONDARY')),
    PRIMARY KEY (order_id, sku_id, sales_type),
    FOREIGN KEY (date) REFERENCES dim_date(date),
    FOREIGN KEY (customer_id) REFERENCES dim_customer(customer_id),
    FOREIGN KEY (region_code) REFERENCES dim_region(region_code),
    FOREIGN KEY (sku_id) REFERENCES dim_sku(sku_id)
);

CREATE TABLE fact_inventory (
    date DATE NOT NULL,
    sku_id TEXT NOT NULL,
    warehouse TEXT NOT NULL,
    region_code TEXT NOT NULL,
    opening_stock INTEGER NOT NULL CHECK (opening_stock >= 0),
    received_units INTEGER NOT NULL CHECK (received_units >= 0),
    available_stock INTEGER NOT NULL CHECK (available_stock >= 0),
    ordered_units INTEGER NOT NULL CHECK (ordered_units >= 0),
    fulfilled_units INTEGER NOT NULL CHECK (fulfilled_units >= 0),
    stockout_flag INTEGER NOT NULL CHECK (stockout_flag IN (0, 1)),
    PRIMARY KEY (date, sku_id, warehouse),
    FOREIGN KEY (date) REFERENCES dim_date(date),
    FOREIGN KEY (sku_id) REFERENCES dim_sku(sku_id),
    FOREIGN KEY (region_code) REFERENCES dim_region(region_code)
);

CREATE TABLE fact_targets (
    date DATE NOT NULL,
    region_code TEXT NOT NULL,
    category TEXT NOT NULL,
    channel TEXT NOT NULL,
    sales_target REAL NOT NULL CHECK (sales_target >= 0),
    order_target INTEGER NOT NULL CHECK (order_target >= 0),
    fill_rate_target REAL NOT NULL CHECK (fill_rate_target >= 0 AND fill_rate_target <= 1),
    PRIMARY KEY (date, region_code, category, channel),
    FOREIGN KEY (date) REFERENCES dim_date(date),
    FOREIGN KEY (region_code) REFERENCES dim_region(region_code)
);

-- Grain: one date x key account manager quota.
-- KAM quotas are a separate planning artifact from the commercial plan in
-- fact_targets: a KAM owns a portfolio of customers rather than a region or
-- a category, so forcing the quota into that grain would leave most
-- combinations empty.
-- Key must match validation.contracts.TABLE_CONTRACTS['fact_kam_targets'].
CREATE TABLE fact_kam_targets (
    date DATE NOT NULL,
    kam_id TEXT NOT NULL,
    sales_target REAL NOT NULL CHECK (sales_target >= 0),
    order_target INTEGER NOT NULL CHECK (order_target >= 0),
    PRIMARY KEY (date, kam_id),
    FOREIGN KEY (date) REFERENCES dim_date(date),
    FOREIGN KEY (kam_id) REFERENCES dim_kam(kam_id)
);

CREATE INDEX idx_sales_date_region ON fact_sales(date, region_code);
CREATE INDEX idx_sales_sku ON fact_sales(sku_id);
CREATE INDEX idx_orders_date_region ON fact_orders(date, region_code);
CREATE INDEX idx_inventory_date_region ON fact_inventory(date, region_code);
CREATE INDEX idx_targets_date_region ON fact_targets(date, region_code);
CREATE INDEX idx_kam_targets_date ON fact_kam_targets(date);
