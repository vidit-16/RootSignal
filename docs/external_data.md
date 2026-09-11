# Running on real public data

## Why

A system validated only against the data it generates has proved that it is
self-consistent, not that it works. The obvious objection to any portfolio
analytics project is that the data was built to suit it.

So the pipeline is run against **UCI Online Retail II**: real transactions from
a UK online retailer, December 2009 to December 2011. A million invoice lines,
40 countries, and the data quality of an operational system.

```bash
python scripts/run_external_dataset.py
```

The dataset downloads on first run (~44 MB, no account needed) and is cached.
**Nothing in the analytical layers changed to accommodate it.** Only an adapter
exists.

Source: <https://archive.ics.uci.edu/dataset/502/online+retail+ii> · CC BY 4.0.

## An adapter declares what its dataset cannot answer

This dataset carries sales and nothing else — no order book, no stock, no plan.
The temptation is to synthesise the missing facts so the whole pipeline runs.
That produces a system that appears to work and quietly answers questions the
data cannot support.

Instead the adapter states its capabilities, and unsupported analyses are
skipped with the reason:

```
Available: fact_sales
Absent:    fact_orders, fact_inventory, fact_targets, fact_kam_targets

Can answer:  sales_kpis, forecasting, trend_analysis, driver_decomposition
Cannot answer:
  - fulfilment_analysis needs fact_orders, which this dataset does not contain.
  - supply_signals needs fact_orders, fact_inventory, ...
  - target_variance needs fact_targets, ...
```

**Returns are the sharpest case.** This data has 22,951 return lines, and it
would be easy to map them to `cancelled_units` and compute something called a
fill rate. That number would then read as a supply failure when customers had
simply sent things back. Returns are reported as returns, and fulfilment
analysis is declared unavailable.

## What the real data broke

Three things, none of which the generated data could have found.

### The composite key does not survive real invoices

The business model keys a sales line on `(order_id, sku_id, sales_type)`, and
that key is enforced in both the SQL schema and the Python contracts. It held
throughout development because generated orders carry each product once.

**45,299 real invoice lines repeat a product already on the same invoice** —
usually with a different quantity, sometimes a different price. A real invoice
can genuinely list one product twice.

The adapter consolidates those lines, adding units and weighting price by
quantity so volume and revenue are unchanged, and records how many rows that
affected. The alternative — adding a line number to the key — would be a
reasonable modelling choice, and is noted here as one the project did not take.

### Cleaning had no rule for a line that cannot be a sale

Real transaction data carries samples, adjustments and corrections priced at
zero, and occasionally below it. The cleaning rules had been written against the
defects the generator seeds, and none of them covered this.

`fact_sales` lines priced at or below zero, or with no units, are now
quarantined: they cannot be repaired into a sale without inventing a price
nobody paid. **2,590 lines** were quarantined on the real dataset. The rule is
inert on generated data, which contains none.

### An empty fact left a column untyped

With no order fact, the mart's outer join produced object-dtype columns that
were then filled and used in arithmetic. It warned rather than failed, and would
have degraded quietly. Values are now coerced before filling.

## What it found

| | Generated | Real |
| --- | --- | --- |
| History | 60 days | **739 days** |
| Sales lines | 7,134 | **1,044,420** |
| Backtest folds | 4 | **49** |
| Scored observations | 28 | **343** |

**Validation and cleaning**, on the data exactly as published:

- 3 errors found — negative prices and a reconciliation failure
- 2,590 unsellable lines quarantined
- **0 errors after cleaning**

**Forecast accuracy** on daily revenue, 49 folds:

| Method | WAPE | vs naive |
| --- | --- | --- |
| **seasonal_mean_7** | **0.4127** | **+38.8%** |
| seasonal_naive_7 | 0.4157 | +38.4% |
| ses | 0.5320 | +21.1% |
| moving_average_7 | 0.5329 | +21.0% |
| naive | 0.6745 | baseline |

`seasonal_mean_7` wins here as it does on the generated data — two unrelated
datasets, the same conclusion. The **absolute** error is far higher (0.41
against 0.076) because real daily retail revenue is enormously more volatile:
the retailer is closed some days and Christmas dominates the year. The
improvement over the baseline is the transferable result; the error level is a
property of the business.

**Driver decomposition** on the latest month's +354,517 movement attributes
**83.5% to the United Kingdom**, with the Netherlands, Australia and Singapore
offsetting slightly — a concentration a reader could act on.

## Limitations

1. **Half the system is not exercised.** Fill rate, inventory, targets, KAM
   performance and the signal engine all need facts this dataset lacks. The
   generated dataset remains the only one that exercises the full pipeline.
2. **Two dimensions are approximations.** Country stands in for region, and
   category is derived from the first word of a product description. Both are
   labelled as approximations in the capability notes.
3. **One external dataset.** Two would be better evidence of generality than
   one, and the adapter interface exists so that adding another is a mapping
   rather than a rewrite.
4. **The download needs network access**, so it is a script rather than part of
   the test suite. The adapter tests use a small in-memory fixture shaped like
   the real file, including its defects.
