"""RootSignal dashboard.

A metric changed. What changed, where did it happen, how large is the impact,
and what should be investigated next?
"""

from __future__ import annotations

import streamlit as st

from shared import caveat, configure, get_data, get_overview, metric_row, sidebar

configure("Home")
input_dir, period = sidebar()

st.title("RootSignal")
st.markdown(
    "**A metric changed. What changed, where did it happen, how large is the "
    "impact, and what should be investigated next?**"
)

state = get_overview(input_dir, period)
if not state["available"]:
    st.warning("Not enough complete periods in this dataset to compare against.")
    st.stop()

st.subheader(f"Latest complete {period}: {state['period'].date()}")
metric_row(
    state["headline"],
    [
        ("net_sales", "Net sales", "{:,.0f}"),
        ("order_count", "Orders", "{:,.0f}"),
        ("sales_units", "Units", "{:,.0f}"),
        ("fill_rate", "Fill rate", "{:.1%}"),
        ("aov", "Average order value", "{:,.2f}"),
    ],
)
caveat(f"Change is against the previous complete {period} ({state['comparison_period'].date()}). Partial periods are excluded.")

st.divider()
left, right = st.columns([2, 1])
with left:
    st.markdown("#### What this dashboard does")
    st.markdown(
        """
        - **Sales** — how trade is running, cut by region, category, channel and manager.
        - **Supply** — fulfilment, stock and the SKUs carrying unfulfilled demand.
        - **Forecasting** — how accurate the expected baseline is, measured out-of-sample.
        - **Root Signals** — what deserves investigating, with the evidence behind it.

        Every figure is computed by the tested analytical layers. This page renders
        results; it does not calculate them.
        """
    )
with right:
    st.markdown("#### Data quality")
    data = get_data(input_dir)
    audit = data["audit"]
    if audit.empty:
        st.success("No cleaning changes were required.")
    else:
        st.dataframe(audit[["table", "action", "rows"]], hide_index=True, use_container_width=True)
    caveat("Quarantined rows are excluded from every figure shown here.")
