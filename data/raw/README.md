# RootSignal sample data

The sample business dataset is generated locally with:

```bash
python scripts/generate_sample_data.py --output-dir data/raw
```

The generator is deterministic with seed `42` and produces 60 daily periods from 2026-01-01 through 2026-03-01.

## Tables

| Table | Grain | Purpose |
|---|---|---|
| `dim_date` | One row per date | Calendar reporting attributes |
| `dim_region` | One row per region | Standard geography |
| `dim_sku` | One row per SKU | Product and pricing attributes |
| `dim_customer` | One row per customer | Account, channel, region, and KAM ownership |
| `dim_kam` | One row per KAM | Account ownership |
| `fact_sales` | One row per commercial sales line | Primary/secondary sales and revenue |
| `fact_orders` | One row per customer order line | Demand, fulfillment, cancellation |
| `fact_inventory` | One row per SKU × warehouse × day | Inventory, availability, and stockouts |
| `fact_targets` | One row per date × region × category × channel target | Plan vs actual comparisons |

## Controlled scenario

From 2026-02-18 onward, the Bengaluru fruit and vegetable segment contains a deliberate operating deterioration: demand stays comparatively firm while inventory and fulfillment weaken. This gives later analytics stages a reproducible business event to detect and investigate.

## Controlled raw-data defects

The generator intentionally adds a small set of defects to raw fact tables:

- two duplicate sales rows
- one missing sales discount value
- one missing sales unit price
- one order where fulfilled units exceed ordered units
- one missing order channel

These are intentional. The upcoming validation stage is responsible for identifying and handling them; the generator must not silently clean them.
