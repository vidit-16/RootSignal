"""Supply: where deliveries are slipping, and what it is costing."""

from __future__ import annotations

import streamlit as st

from components.charts import comparison_bars, trend_line, variance_bars
from rootsignal.presentation import humanise_identifiers, humanise_segment, label_for
from shared import caveat, configure, get_names, get_supply, glossary, show_table, sidebar

configure("Supply")
input_dir, period = sidebar()
views = get_supply(input_dir, period)
names = get_names(input_dir)

st.title("Supply and delivery")
st.caption("Are we delivering what customers ordered — and if not, where is it going wrong?")

service = views["service"]
latest = service[service["period_start"] == service["period_start"].max()]
below = latest[latest["variance"] < 0]
unfulfilled = int((views["segments"]["ordered_units"] - views["segments"]["fulfilled_units"]).sum())

left, right, third = st.columns(3)
left.metric("Segments missing the delivery target", f"{len(below)} of {len(latest)}")
right.metric(
    "Worst shortfall against target",
    f"{below['variance'].min():.1%}" if len(below) else "none",
)
third.metric("Units ordered but not delivered", f"{unfulfilled:,}")
caveat(
    "A segment nobody ordered from has no fill rate, and is not counted as a miss. "
    f"Figures cover the complete {period}s in the dataset."
)

st.divider()
st.subheader("Share of orders actually delivered")
st.plotly_chart(
    trend_line(
        humanise_identifiers(views["by_region"], names),
        "period_start", "fill_rate", series="region_code", title="By region",
    ),
    use_container_width=True,
)
caveat("1.00 means every unit ordered was delivered. The service target is 0.93.")

gap = latest.copy()
gap["segment"] = gap["segment"].map(lambda s: humanise_segment(s, names))
st.plotly_chart(
    variance_bars(
        gap.nsmallest(12, "variance"), "segment", "variance",
        title=f"Distance from the delivery target, {latest['period_start'].max().date()}",
    ),
    use_container_width=True,
)
caveat("Red is below target, green above. A bar of -0.20 means twenty points below the target rate.")

st.divider()
st.subheader("Stock on hand")
inventory = humanise_identifiers(views["inventory_by_region"], names)
left, right = st.columns(2)
with left:
    st.plotly_chart(
        trend_line(inventory, "period_start", "available_stock", series="region_code",
                   title="Average stock available"),
        use_container_width=True,
    )
with right:
    st.plotly_chart(
        trend_line(inventory, "period_start", "stockout_rate", series="region_code",
                   title="How often products ran out"),
        use_container_width=True,
    )
caveat(
    "Stock is a level, so it is averaged over the period; deliveries into the warehouse are "
    "a flow, so they are added up. Adding stock up across days would report a week's stock "
    "as seven times its real size."
)

st.divider()
st.subheader("Products with the most unserved demand")
by_sku = views["by_sku"]
worst = by_sku.groupby(["sku_id", "sku_name", "category"], as_index=False)[
    ["ordered_units", "fulfilled_units", "unfulfilled_units"]
].sum()
worst["fill_rate"] = (worst["fulfilled_units"] / worst["ordered_units"]).round(4)
worst = worst.nlargest(15, "unfulfilled_units")

st.plotly_chart(
    comparison_bars(worst, "sku_name", "unfulfilled_units", title="Units ordered but not delivered"),
    use_container_width=True,
)
show_table(worst, input_dir, drop=("sku_id",))
caveat(
    "Ranked by how many units went unserved, not by delivery rate. A poor rate on a product "
    "nobody orders matters less than a modest one on a product everybody does."
)

glossary([label_for(c) for c in ("fill_rate", "stockout_rate", "unfulfilled_units")])
