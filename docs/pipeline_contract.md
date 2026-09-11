# End-to-End Pipeline Contract

The RootSignal baseline is validated as one connected pipeline:

`generate → load → validate → clean → validate → consolidate → reconcile`

The generated raw dataset intentionally contains controlled defects, so raw validation is expected to fail. Cleaning must repair only unambiguous defects and quarantine unsafe orders. The cleaned dataset must then pass validation.

The end-to-end test also verifies that the commercial mart does not change total net sales, sales units, ordered units, or fulfilled units. This protects against accidental row multiplication or loss during enrichment and aggregation.

The test is intentionally run in the normal `pytest` suite. CI therefore treats the full pipeline contract as part of the baseline rather than as an optional downstream check.

## What the contract asserts now

The end-to-end test additionally checks that order counts behave correctly at a
grain that splits orders. The commercial mart splits by category and orders are
baskets, so summing `sales_order_count` across the mart legitimately exceeds the
distinct order count, while no single cell may exceed it. Daily distinct counts
do add up, because an order belongs to one date.

The generated dataset also carries four labelled scenarios and their evaluation
windows in `dataset_manifest.json`, so the signal engine can be scored against
what was actually planted rather than against a restatement of it.
