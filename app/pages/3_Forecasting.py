"""Forecasting: how close the expected baseline gets, and on what evidence."""

from __future__ import annotations

import streamlit as st

from components.charts import actual_versus_expected, comparison_bars, trend_line
from rootsignal.presentation import describe_model, label_for
from shared import caveat, configure, get_forecasts, glossary, show_table, sidebar

configure("Forecasting")
input_dir, _ = sidebar(show_period=False)
views = get_forecasts(input_dir)
settings = views["settings"]

METRIC_NAMES = {"net_sales": "Net sales", "units": "Units sold", "orders": "Orders"}

st.title("Forecasting")
st.caption(
    "A forecast gives us a number to compare against. Without one we cannot tell an "
    "ordinary quiet week from a week that genuinely went wrong."
)

metric = st.selectbox(
    "What to forecast",
    list(views["accuracy"]),
    index=0,
    format_func=lambda m: METRIC_NAMES.get(m, m),
)
accuracy = views["accuracy"][metric]
backtest = views["backtests"][metric]
best = accuracy.iloc[0]

left, right, third = st.columns(3)
left.metric("Best method", describe_model(best["model"]))
right.metric("Typical error", f"{best['wape']:.1%}")
third.metric(
    "Better than repeating yesterday",
    f"{best['wape_improvement_vs_baseline']:+.1%}"
    if best["model"] != "naive"
    else "this is the baseline",
)
caveat(
    f"Tested by forecasting {settings['horizon']} days ahead from "
    f"{backtest['fold'].nunique()} different starting points, each time using only data from "
    f"before the days being predicted. Nothing here is scored on data the method had seen."
)

st.divider()
st.subheader("How the methods compare")
labelled = accuracy.copy()
labelled["model"] = labelled["model"].map(describe_model)
st.plotly_chart(
    comparison_bars(labelled, "model", "wape", title="Typical error, shorter is better"),
    use_container_width=True,
)
show_table(
    accuracy[
        ["model", "folds", "n_observations", "mae", "rmse", "wape", "bias", "wape_improvement_vs_baseline"]
    ],
    input_dir,
)
caveat(
    "A method that scores worse than the baseline is shown as such rather than hidden. "
    "Repeating last week performs badly here, and that is a finding rather than a fault."
)

st.divider()
st.subheader(f"What happened, against what {describe_model(best['model']).lower()} predicted")
st.plotly_chart(actual_versus_expected(backtest, "date", "actual", "forecast"), use_container_width=True)
st.plotly_chart(
    trend_line(backtest, "date", "error", title="How far off it was (above zero means it predicted too high)"),
    use_container_width=True,
)
caveat(
    "These are predictions for days the method had not seen. Accuracy is measured for the "
    "business as a whole, one day at a time. Forecasts for individual regions or categories "
    "have not been tested and should not be assumed to be this good."
)

with st.expander("The daily numbers being forecast"):
    show_table(views["series"].reset_index().rename(columns={"index": "date"}), input_dir)

glossary([label_for(column) for column in ("wape", "wape_improvement_vs_baseline")])
