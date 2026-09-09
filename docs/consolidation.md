# Commercial consolidation layer

RootSignal keeps operational facts at their original grains and does not join them directly when both contain repeated business entities.

## Enrichment

`enrich_sales` keeps `fact_sales` at one commercial sales transaction-line grain while adding product, customer, KAM, and region attributes.

`enrich_orders` keeps `fact_orders` at one customer order-line grain while adding product, customer, and region attributes.

Dimension joins use `many_to_one` validation and explicitly reject duplicate dimension keys. The row count of each fact is also checked after enrichment so a bad dimension cannot silently multiply facts.

## Commercial mart

`build_commercial_mart` creates a common analytical grain:

`date × region_code × category × channel × sales_type`

The sales and order facts are aggregated independently to that grain first. Only then are the two aggregate tables merged with a one-to-one validation.

This means an order containing multiple SKU lines is still counted once for order-count metrics while its units and sales are summed across its valid lines.

The mart exposes:

- gross sales
- net sales
- sales units
- distinct sales order count
- distinct order count
- ordered, fulfilled, and cancelled units
- fill rate
- cancellation rate
- average order value
- discount value

Zero ordered units produce an undefined fill rate and cancellation rate rather than a fabricated percentage.

## Why this matters

This layer is the bridge between cleaned operational data and downstream forecasting, variance analysis, driver decomposition, and root-signal generation. The same common grain can later be compared with compatible target data without creating fact-to-fact multiplication.
