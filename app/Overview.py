"""RootSignal dashboard.

A metric changed. What changed, where did it happen, how large is the impact,
and what should be investigated next?
"""

from __future__ import annotations

import streamlit as st

from shared import (
    caveat,
    configure,
    full_glossary,
    get_data,
    get_overview,
    metric_row,
    show_table,
    sidebar,
)

configure("Home")
input_dir, period = sidebar()

st.title("RootSignal")
st.markdown(
    "**Something in the numbers moved. What moved, where, how much is it worth, "
    "and what should you look at first?**"
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
        ("sales_units", "Units sold", "{:,.0f}"),
        ("fill_rate", "Orders delivered", "{:.1%}"),
        ("aov", "Average order value", "{:,.0f}"),
    ],
)
caveat(f"Change is against the previous complete {period} ({state['comparison_period'].date()}). Partial periods are excluded.")

st.divider()
left, right = st.columns([2, 1])
with left:
    st.markdown("#### What this dashboard does")
    st.markdown(
        """
        - **Sales** — how much we sold, to whom, through which route, and against plan.
        - **Supply** — whether we delivered what was ordered, and which products fell short.
        - **Forecasting** — how close we can get to predicting a normal week.
        - **What to look into** — the movements worth someone's time, with the evidence.

        Nothing on these pages is calculated here. Every figure comes from the tested
        analysis underneath, so a number on screen matches the same number in a report
        or a database query.
        """
    )
with right:
    st.markdown("#### Data quality")
    data = get_data(input_dir)
    audit = data["audit"]
    if audit.empty:
        st.success("No cleaning changes were required.")
    else:
        show_table(audit[["table", "action", "rows"]], input_dir)
    caveat(
        "Rows that could not be safely repaired were set aside and are excluded from "
        "every figure on these pages."
    )

st.divider()
full_glossary()
