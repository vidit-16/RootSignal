"""Sales: how trade is running, and where."""

from __future__ import annotations

import streamlit as st

from components.charts import comparison_bars, stacked_mix, trend_line, variance_bars
from rootsignal.presentation import humanise_identifiers, humanise_segment, label_for
from shared import caveat, configure, get_names, get_sales, glossary, show_table, sidebar

configure("Sales")
input_dir, period = sidebar()
views = get_sales(input_dir, period)
names = get_names(input_dir)

st.title("Sales")
st.caption("How much we sold, to whom, through which route — and against plan.")

st.subheader("Net sales by day")
st.plotly_chart(
    trend_line(views["daily"].reset_index(), "period_start", "net_sales"),
    use_container_width=True,
)
caveat("Every trading day in the dataset, including any part-weeks at either end.")

st.divider()
st.subheader(f"Where the sales came from (by {period})")
tabs = st.tabs(["Region", "Category", "Channel", "Account manager"])
for tab, (key, dimension, noun) in zip(
    tabs,
    [
        ("by_region", "region_code", "region"),
        ("by_category", "category", "category"),
        ("by_channel", "channel", "channel"),
        ("by_kam", "kam_id", "account manager"),
    ],
):
    with tab:
        frame = views[key]
        totals = frame.groupby(dimension, as_index=False)[
            ["net_sales", "sales_units", "order_count"]
        ].sum()
        named_totals = humanise_identifiers(totals, names)
        named_frame = humanise_identifiers(frame, names)

        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                comparison_bars(named_totals, dimension, "net_sales", title=f"Net sales by {noun}"),
                use_container_width=True,
            )
        with right:
            st.plotly_chart(
                trend_line(named_frame, "period_start", "net_sales", series=dimension, title="Over time"),
                use_container_width=True,
            )
        show_table(totals.sort_values("net_sales", ascending=False), input_dir)
        caveat(
            "Each tab is the whole business seen from a different angle. The four tabs "
            "describe the same sales, so figures from different tabs should never be added together."
        )

st.divider()
st.subheader("Against plan")
plan = views["versus_plan"].copy()
plan["segment"] = plan["segment"].map(lambda s: humanise_segment(s, names))
worst = plan.nsmallest(12, "variance")

st.plotly_chart(
    variance_bars(worst, "segment", "variance", title="Biggest shortfalls against target"),
    use_container_width=True,
)
caveat("Red is behind plan, green ahead. Sorted by size of the gap, not by how big the segment is.")
show_table(
    plan[["period_start", "segment", "actual", "comparison_value", "variance", "variance_pct"]]
    .sort_values("variance"),
    input_dir,
)

st.divider()
st.subheader("Sell-in and sell-through")
mix = humanise_identifiers(views["mix"], names)
st.plotly_chart(
    stacked_mix(mix, "region_code", ["primary_sales", "secondary_sales"], title="By region"),
    use_container_width=True,
)
show_table(views["mix"], input_dir)
caveat(
    "Primary is what the trade bought from us; secondary is what their customers bought "
    "from them. They are different events and adding them together would double-count demand."
)

glossary([label_for(c) for c in ("primary_sales", "secondary_sales", "aov", "variance")])
