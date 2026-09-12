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

The generated dataset also carries five labelled scenarios and their evaluation
windows in `dataset_manifest.json`, so the signal engine can be scored against
what was actually planted rather than against a restatement of it.

## The contract is enforced, not only asserted

For a long time the pipeline above described what the *test* did. The
command-line tools did something shorter. Every entry point but
`run_external_dataset.py` composed the loader and the cleaner directly —
`clean_dataset(load_dataset(...))` — and went straight to analysis, so the two
validation steps in the diagram happened in `tests/test_pipeline_e2e.py` and
nowhere else. A dataset with a column missing from `fact_sales` loaded without
complaint and produced signals, a briefing and six workbooks from it.

That is a strange shape for this project in particular. The explanation layer
refuses to publish a figure it cannot trace back to the evidence; the input
side would accept almost anything. A guarantee on the way out is worth much
less when the way in is unguarded.

`rootsignal.dataset.load_for_analysis` is now the single way in, and every
script and the dashboard go through it. It loads, cleans, and then checks the
contracts, raising `DatasetContractError` — naming each failing check — if any
error survives cleaning.

**Errors are judged after cleaning, never before.** Raw validation is expected
to fail: the generated data carries planted defects, and the published Online
Retail II data has negative prices and rows that do not reconcile. Both reach
zero errors once cleaned. An error that survives cleaning is not dirty data —
it is data the analysis code was not written against.

Warnings never refuse a dataset. The cleaned sample still carries a
stockout-flag warning, and a check that refused on warnings would refuse the
project's own data and teach everyone to switch it off.

`tests/test_dataset.py` covers the refusal, the message, and `strict=False`. It
also scans `scripts/` for the old pattern, so a new script written from the
shape of an old one cannot quietly reopen the gap.
