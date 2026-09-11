"""Supply: fulfilment, stock, and where demand is going unserved."""

from __future__ import annotations

import streamlit as st

from components.charts import STATUS, comparison_bars, trend_line, variance_bars
from shared import caveat, configure, get_supply, sidebar

configure("Supply")
input_dir, period = sidebar()
views = get_supply(input_dir, period)

st.title("Supply and fulfilment")

service = views["service"]
latest = service[service["period_start"] == service["period_start"].max()]
below = latest[latest["variance"] < 0]

left, right, third = st.columns(3)
left.metric("Segments below service target", f"{len(below)} of {len(latest)}")
right.metric(
    "Worst gap to target",
    f"{below['variance'].min():.1%}" if len(below) else "none",
)
third.metric(
    "Unfulfilled units in period",
    f"{int((views['segments']['ordered_units'] - views['segments']['fulfilled_units']).sum()):,}",
)
caveat("A period with no demand has no fill rate and is not counted as a miss.")

st.divider()
st.subheader("Fill rate against the service target")
st.plotly_chart(
    trend_line(views["by_region"], "period_start", "fill_rate", series="region_code", title="By region"),
    use_container_width=True,
)
st.plotly_chart(
    variance_bars(
        latest.nsmallest(12, "variance"), "segment", "variance",
        title=f"Gap to target, {latest['period_start'].max().date()}",
    ),
    use_container_width=True,
)

st.divider()
st.subheader("Stock and stockouts")
inventory = views["inventory_by_region"]
left, right = st.columns(2)
with left:
    st.plotly_chart(
        trend_line(inventory, "period_start", "available_stock", series="region_code", title="Average available stock"),
        use_container_width=True,
    )
with right:
    st.plotly_chart(
        trend_line(inventory, "period_start", "stockout_rate", series="region_code", title="Stockout rate"),
        use_container_width=True,
    )
caveat(
    "Stock is a level and is averaged across the period; receipts and movements are "
    "flows and are summed. Summing a level would report a week's stock as seven times its size."
)

st.divider()
st.subheader("SKUs carrying the unfulfilled demand")
by_sku = views["by_sku"]
worst = by_sku.groupby(["sku_id", "sku_name", "category"], as_index=False)[
    ["ordered_units", "fulfilled_units", "unfulfilled_units"]
].sum()
worst["fill_rate"] = (worst["fulfilled_units"] / worst["ordered_units"]).round(4)
worst = worst.nlargest(15, "unfulfilled_units")
st.plotly_chart(
    comparison_bars(worst, "sku_name", "unfulfilled_units", title="Unfulfilled units by SKU"),
    use_container_width=True,
)
st.dataframe(worst, hide_index=True, use_container_width=True)
caveat(
    f"Ranked by volume of unserved demand, not by fill rate: a low rate on a tiny SKU "
    f"matters less than a modest one on a large SKU. Status colours here are reserved "
    f"({', '.join(STATUS)}) and always carry a label."
)
