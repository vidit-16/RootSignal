# Cleaning and consolidation rules

RootSignal separates validation from cleaning. Validation reports what is wrong in the raw data; cleaning applies only documented, low-risk transformations and quarantines unsafe rows.

## Recoverable transformations

- Exact duplicate dimension rows are removed.
- Exact duplicate sales transaction rows are removed.
- Missing `fact_sales.discount_pct` is treated as 0.0 because the field has a defined percentage meaning and no discount is the least-assumptive default.
- Missing `fact_sales.unit_price` is recovered from `dim_sku.list_price` when the SKU resolves.
- Missing `fact_orders.channel` is recovered from `dim_customer.channel` when the customer resolves.
- Duplicate inventory snapshot keys keep the first row and emit an audit event.
- Duplicate target and KAM-quota keys keep the first row and emit an audit event.
- Duplicate target keys keep the first row and emit an audit event.

## Quarantine rules

Rows with impossible order quantities (`fulfilled_units > ordered_units`) are not silently corrected. They are removed from the clean analytical table and written to the quarantine output with an audit event.

Other unresolved defects remain visible through validation and must be resolved before they are allowed into downstream KPI calculations.

## Audit contract

Every applied transformation is recorded as:

`table | action | rows | detail`

This lets users see what changed between raw and clean data without losing traceability.

## Consolidation principle

Consolidation enriches facts from dimensions only at compatible grain. Fact tables are not merged directly with each other before aggregation, preventing accidental row multiplication and double-counting.

## Tables covered

`clean_dataset` requires the full business model and raises rather than
producing a partial result if a table is absent: five dimensions and the five
facts, including `fact_kam_targets`.

Orders are baskets carrying one to three SKU lines, so an `order_id` appears on
several rows of both `fact_sales` and `fact_orders`. Cleaning operates on lines
and never on orders, which is why duplicate removal uses whole-row equality
rather than the order key.
