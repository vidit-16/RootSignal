"""Root Signals: what deserves a look, and how much the evidence supports it."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.charts import comparison_bars
from rootsignal.explanation import write_briefing, write_summary
from rootsignal.presentation import (
    describe_criterion,
    describe_pattern,
    humanise_segment,
    humanise_statement,
    label_for,
)
from shared import (
    caveat,
    configure,
    get_data,
    get_names,
    get_periods,
    get_signals,
    glossary,
    show_table,
    sidebar,
)

configure("Root Signals")
input_dir, period = sidebar()
names = get_names(input_dir)

METRICS = {
    "fill_rate": "Share of orders delivered",
    "net_sales": "Net sales",
    "ordered_units": "Units ordered",
}
FLOORS = {
    "any": "Show everything",
    "low": "Low and above",
    "medium": "Medium and above",
    "high": "Only well-supported",
}

st.title("What to look into")
st.caption(
    "Each item says what the evidence is **consistent with** — not what caused it. "
    "Every one also names the other explanation that would fit, so you can weigh both."
)

periods = get_periods(input_dir, period)
if len(periods) < 2:
    st.warning(f"There are not two complete {period}s in this dataset to compare.")
    st.stop()

controls = st.columns(4)
metric = controls[0].selectbox("Measure", list(METRICS), format_func=METRICS.get)
current = controls[1].selectbox(f"Which {period}", periods, format_func=lambda d: str(d.date()))
earlier = [day for day in periods if day < current]
comparison = (
    controls[2].selectbox("Compared with", earlier, format_func=lambda d: str(d.date()))
    if earlier
    else None
)
floor = controls[3].selectbox(
    "How sure do we need to be?",
    list(FLOORS),
    format_func=FLOORS.get,
    help="Stricter settings stay quiet unless several pieces of evidence agree.",
)

if comparison is None:
    st.warning("This is the earliest period; there is nothing before it to compare with.")
    st.stop()

views = get_signals(
    input_dir,
    metric,
    ("region_code", "category"),
    str(current.date()),
    str(comparison.date()),
    None if floor == "any" else floor,
)
signals = views["signals"]

if not signals:
    st.success(
        f"Nothing stands out for the {period} of {current.date()} at this setting. "
        "A tool that flags something every week is not telling you anything."
    )
    st.stop()

chart = views["table"].copy()
chart["segment"] = chart["segment"].map(lambda segment: humanise_segment(segment, names))
st.plotly_chart(
    comparison_bars(chart, "segment", "impact", title="What each is worth, roughly"),
    use_container_width=True,
)
caveat(
    "Impact is the revenue attached to demand that went unserved. It is an estimate of what "
    "was at stake, not money we can prove was lost."
)

tables = get_data(input_dir)["tables"]
st.markdown("#### In short")
st.markdown(write_summary([signal.as_dict() for signal in signals], tables))

for position, signal in enumerate(signals):
    readable = humanise_segment(signal.segment, names)
    with st.expander(
        f"{position + 1}. {readable} — {describe_pattern(signal.pattern.pattern)}",
        expanded=position == 0,
    ):
        left, right, third = st.columns(3)
        left.metric(
            "Change",
            f"{signal.movement:+.1%}" if abs(signal.movement) < 1 else f"{signal.movement:+,.0f}",
        )
        right.metric("Roughly worth", f"{signal.impact.value:,.0f}" if signal.impact else "n/a")
        third.metric(
            "How well supported",
            f"{signal.confidence.level.title()} "
            f"({signal.confidence.met} of {signal.confidence.total} checks)",
        )

        briefing = write_briefing(signal.as_dict(), tables)
        st.markdown(briefing.as_markdown())

        def readable_text(text: str) -> str:
            return humanise_statement(text, signal.segment, names)

        st.warning(
            f"**The other possibility:** {readable_text(signal.pattern.alternative_hypothesis)} "
            f"{readable_text(signal.pattern.alternative_note)}"
        )
        st.info(
            f"**What to check next:** {readable_text(signal.pattern.recommended_investigation)}"
        )

        tabs = st.tabs(["The numbers behind it", "The checks", "How the estimate was made"])
        with tabs[0]:
            evidence = views["evidence"]
            show_table(
                evidence[evidence["segment"] == signal.segment], input_dir, drop=("segment",)
            )
        with tabs[1]:
            criteria = views["criteria"]
            rows = criteria[criteria["segment"] == signal.segment]
            for _, row in rows.iterrows():
                mark = "✅" if row["met"] else "⬜"
                st.markdown(
                    f"{mark} **{describe_criterion(row['criterion'])}** — {row['detail']}"
                )
            caveat(
                "How well supported is a count of these six checks. It is not a probability, "
                "and none of them is a statistical test."
            )
        with tabs[2]:
            if signal.impact is None:
                st.write("No estimate could be made for this one.")
            else:
                st.markdown(f"**Worked out as:** {signal.impact.basis}")
                show_table(pd.DataFrame([signal.impact.components]), input_dir)
                st.markdown("**Assumptions, all of which make this figure generous:**")
                for assumption in signal.impact.assumptions:
                    st.markdown(f"- {assumption}")

        for note in signal.notes:
            st.caption(f"Note: {note}")

glossary([label_for(column) for column in ("impact", "confidence", "fill_rate")])
