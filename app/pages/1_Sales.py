"""Sales performance: how trade is running, and where."""

from __future__ import annotations

import streamlit as st

from components.charts import comparison_bars, stacked_mix, trend_line, variance_bars
from shared import caveat, configure, get_sales, sidebar

configure("Sales")
input_dir, period = sidebar()
views = get_sales(input_dir, period)

st.title("Sales")

st.subheader("Daily net sales")
st.plotly_chart(
    trend_line(views["daily"].reset_index(), "period_start", "net_sales", title=""),
    use_container_width=True,
)
caveat("Daily values, including any partial periods at the edges of the range.")

st.divider()
st.subheader(f"Performance by segment ({period})")
tabs = st.tabs(["Region", "Category", "Channel", "Key account manager"])
for tab, (key, dimension) in zip(
    tabs,
    [
        ("by_region", "region_code"),
        ("by_category", "category"),
        ("by_channel", "channel"),
        ("by_kam", "kam_id"),
    ],
):
    with tab:
        frame = views[key]
        totals = frame.groupby(dimension, as_index=False)[["net_sales", "sales_units", "order_count"]].sum()
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                comparison_bars(totals, dimension, "net_sales", title="Net sales"),
                use_container_width=True,
            )
        with right:
            st.plotly_chart(
                trend_line(frame, "period_start", "net_sales", series=dimension, title="Over time"),
                use_container_width=True,
            )
        st.dataframe(totals.sort_values("net_sales", ascending=False), hide_index=True, use_container_width=True)
        caveat(
            "Each breakdown is a complete view of the same trade from a different "
            "angle. Figures from different tabs describe the same sales and must not be added together."
        )

st.divider()
st.subheader("Against plan")
plan = views["versus_plan"].copy()
worst = plan.nsmallest(12, "variance")
st.plotly_chart(
    variance_bars(worst, "segment", "variance", title="Largest shortfalls against target"),
    use_container_width=True,
)
st.dataframe(
    plan[["period_start", "segment", "actual", "comparison_value", "variance", "variance_pct"]]
    .rename(columns={"comparison_value": "target"})
    .sort_values("variance"),
    hide_index=True,
    use_container_width=True,
)

st.divider()
st.subheader("Primary and secondary sales")
mix = views["mix"]
st.plotly_chart(
    stacked_mix(mix, "region_code", ["primary_sales", "secondary_sales"], title="Sell-in against sell-through"),
    use_container_width=True,
)
st.dataframe(mix, hide_index=True, use_container_width=True)
caveat("Primary and secondary are different commercial events and are not additive as a measure of end demand.")
