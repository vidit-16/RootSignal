"""Root Signals: what deserves investigating, and how far the evidence goes."""

from __future__ import annotations

import streamlit as st

from components.charts import STATUS, comparison_bars
from shared import caveat, configure, get_periods, get_signals, sidebar

configure("Root Signals")
input_dir, period = sidebar()

st.title("Root Signals")
st.markdown(
    "Each signal states what its evidence is **consistent with**. None of them is a "
    "proven cause, and every one names the leading alternative explanation."
)

periods = get_periods(input_dir, period)
if len(periods) < 2:
    st.warning("Not enough complete periods to compare against.")
    st.stop()

controls = st.columns(4)
metric = controls[0].selectbox("Metric", ["fill_rate", "net_sales", "ordered_units"], index=0)
current = controls[1].selectbox("Period", periods, index=0, format_func=lambda d: str(d.date()))
earlier = [d for d in periods if d < current]
comparison = controls[2].selectbox(
    "Compare against", earlier, index=0, format_func=lambda d: str(d.date())
) if earlier else None
floor = controls[3].selectbox(
    "Minimum confidence", ["any", "low", "medium", "high"], index=0,
    help="A strict floor keeps the engine quiet unless the evidence lines up.",
)

if comparison is None:
    st.warning("No earlier period to compare against.")
    st.stop()

views = get_signals(
    input_dir, metric, ("region_code", "category"),
    str(current.date()), str(comparison.date()),
    None if floor == "any" else floor,
)
signals = views["signals"]

if not signals:
    st.success(
        f"No signals at this confidence floor for {current.date()}. "
        "A detector that flags something every period is not detecting anything."
    )
    st.stop()

st.plotly_chart(
    comparison_bars(views["table"], "segment", "impact", title="Estimated impact by segment"),
    use_container_width=True,
)

for position, signal in enumerate(signals):
    colour = STATUS["critical"] if signal.confidence.level == "high" else STATUS["warning"]
    with st.expander(
        f"{position + 1}. {signal.segment} — {signal.pattern.pattern} "
        f"({signal.confidence.level} confidence)",
        expanded=position == 0,
    ):
        left, right, third = st.columns(3)
        left.metric("Movement", f"{signal.movement:+.4f}")
        right.metric("Estimated impact", f"{signal.impact.value:,.2f}" if signal.impact else "n/a")
        third.metric(
            "Confidence",
            f"{signal.confidence.level} ({signal.confidence.met}/{signal.confidence.total})",
        )

        st.markdown(f"**{signal.pattern.statement}**")
        if signal.pattern.corroborating:
            st.markdown("Supporting evidence: " + "; ".join(signal.pattern.corroborating) + ".")

        st.markdown(
            f":orange[**Alternative considered:**] {signal.pattern.alternative_hypothesis} "
            f"{signal.pattern.alternative_note}"
        )
        st.info(f"**Recommended investigation:** {signal.pattern.recommended_investigation}")

        evidence = views["evidence"]
        criteria = views["criteria"]
        tabs = st.tabs(["Evidence", "Confidence criteria", "Impact basis"])
        with tabs[0]:
            st.dataframe(
                evidence[evidence["segment"] == signal.segment].drop(columns=["segment"]),
                hide_index=True, use_container_width=True,
            )
        with tabs[1]:
            st.dataframe(
                criteria[criteria["segment"] == signal.segment].drop(columns=["segment"]),
                hide_index=True, use_container_width=True,
            )
            caveat("Confidence is a count of these criteria. It is not a probability, and none of them is a statistical test.")
        with tabs[2]:
            if signal.impact is None:
                st.write("No impact estimate available for this signal.")
            else:
                st.write(f"**Basis:** {signal.impact.basis}")
                st.json(signal.impact.components)
                for assumption in signal.impact.assumptions:
                    st.caption(f"• {assumption}")

        for note in signal.notes:
            st.caption(f"Note: {note}")
