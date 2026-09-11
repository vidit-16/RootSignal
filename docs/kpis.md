# RootSignal KPI definitions

The KPI layer is deterministic. It consumes cleaned fact tables and computes business metrics without using an LLM for numeric truth.

## Sales KPIs

`gross_sales = units * unit_price`

`discount_value = gross_sales - net_sales`

`order_count = COUNT(DISTINCT order_id)`

`AOV = net_sales / distinct order count`

Because `fact_sales` is an order-line table, order count must use distinct order IDs after the requested aggregation. Summing a line-level order count would double-count multi-line orders.

## Order KPIs

`fill_rate = fulfilled_units / ordered_units`

Periods with zero ordered units are treated as undefined rather than 100% or 0% because there is no demand against which fulfillment can be measured.

`cancellation_rate = cancelled_units / ordered_units`

## Growth

Growth is calculated against the immediately preceding available reporting period within the selected grouping dimensions.

`growth_pct = (current - previous) / previous`

A zero previous value produces an undefined growth percentage to avoid fabricated infinities.

## Primary / secondary sales

Primary and secondary sales share the sales fact structure but remain separately queryable through `sales_type`.

`primary_mix = primary_sales / total_sales`

`secondary_mix = secondary_sales / total_sales`

## Target variance

Actuals and targets must first be aggregated to a compatible grain. They are then joined on that explicit grain.

`variance = actual - target`

`variance_pct = variance / target`

The KPI engine does not join raw sales facts directly to raw target facts because mismatched grain can multiply rows and inflate results.

## Cross-stage contract

1. The validation/cleaning layer establishes a clean fact table.
2. The KPI layer consumes only that cleaned representation.
3. KPI functions accept explicit `group_by` dimensions so the caller controls analytical grain.
4. The forecasting and driver-analysis layers should consume these aggregated metrics rather than recomputing them independently.
5. The dashboard and Excel reporting layers should display these KPI outputs without changing their definitions.

## Order counts are not additive across every grain

`order_count` is a distinct count of `order_id`, correct at whatever grain it is
computed. It is **not** safe to sum across a breakdown that splits an order.

Orders are baskets, so one order can carry a fruit line and a vegetable line.
At a grain including `category` that order sits in two cells, counted once in
each — correct in both, double-counted the moment they are added. Summing the
daily mart's `order_count` across categories reported 6,667 orders against an
actual 4,846 until it was fixed.

Units, sales and other quantities are additive. Counts of a thing that spans
cells are not, and a company-level order count must be a distinct count taken
from the fact.
