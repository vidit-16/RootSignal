# RootSignal business data model

## Design principle

The analytical model separates reusable dimensions from operational facts. Every fact table has an explicit grain so metrics can be calculated without accidental double-counting.

## Dimensions

### `dim_date`
One row per calendar date used for reporting. Supports day, week, month, quarter, and year rollups.

### `dim_sku`
One row per sellable SKU. Stores product hierarchy, pack size, cost, list price, and active status.

### `dim_customer`
One row per downstream business/customer account. Stores customer type, sales channel, region, and assigned KAM.

### `dim_kam`
One row per key-account manager. Keeps commercial ownership separate from transaction data.

### `dim_region`
One row per operating region/city. Keeps geography consistent across sales, orders, inventory, and targets.

## Facts

### `fact_sales`
**Grain:** one commercial sales transaction line.
**Key:** `(order_id, sku_id, sales_type)`

Supports both `PRIMARY` and `SECONDARY` sales through `sales_type`. Each row records SKU, customer, KAM, geography, channel, units, pricing, discounts, and net sales.

### `fact_orders`
**Grain:** one customer order line.
**Key:** `(order_id, sku_id, sales_type)`

Stores ordered, fulfilled, and cancelled quantities plus order status. This is the operational source for order and fulfillment KPIs.

### `fact_inventory`
**Grain:** one SKU × warehouse/region × day snapshot.
**Key:** `(date, sku_id, warehouse)`

Stores opening stock, receipts, available stock, demand, fulfilled units, and stockout state.

### `fact_targets`
**Grain:** one date × region × category × channel target.
**Key:** `(date, region_code, category, channel)`

Stores sales, order, and fill-rate targets for plan-vs-actual analysis.

## Key relationships

```text
                 dim_date
                    |
          +---------+----------+
          |                    |
      fact_sales          fact_orders
          |                    |
    +-----+-----+              |
    |     |     |              |
 dim_sku dim_customer dim_kam  |
    |     |     |              |
    +-----+-----+--------------+
              |
         dim_region
              |
      +-------+--------+
      |                |
 fact_inventory   fact_targets
```

## Analytical rules

1. Facts are never joined together blindly. Aggregate each fact to a compatible analytical grain before comparing metrics.
2. Revenue and KPI values are deterministic calculations. An LLM may explain evidence but never becomes the numeric source of truth.
3. Fill rate is fulfilled units divided by ordered units, with explicit handling for zero-demand periods.
4. Variance retains both absolute movement and percentage movement and records whether the comparison is against target, prior period, or forecast.
5. Driver contribution is calculated from metric movement before ranking regions, categories, channels, customers, KAMs, or SKUs.
6. Impact estimates must state the assumption used, such as lost units multiplied by realized or representative selling price.
7. Primary and secondary sales remain separately queryable even though they share a common fact structure.
8. Fact keys are declared once and enforced in both places. `sql/schema.sql` and `validation.contracts.TABLE_CONTRACTS` must agree on every key, so the database cannot accept rows the validator rejects. `tests/test_sql_schema.py` fails if they drift apart.
9. `order_id` is deliberately not a key on its own. A single order may carry several SKUs, so keying on it would reject legitimate multi-SKU orders and make order counts ambiguous.

## Data-quality expectations

Before KPI calculations, the pipeline will validate identifiers, dates, numeric ranges, duplicate keys, referential integrity, and cross-table reconciliation.

The sample dataset will contain a small number of intentional quality defects so that validation behavior can be tested without making the analytical scenario itself unreliable.
