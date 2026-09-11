"""Forecasting: how accurate the expected baseline is, and on what evidence."""

from __future__ import annotations

import streamlit as st

from components.charts import actual_versus_expected, comparison_bars, trend_line
from shared import caveat, configure, get_forecasts, sidebar

configure("Forecasting")
input_dir, _ = sidebar(show_period=False)
views = get_forecasts(input_dir)
settings = views["settings"]

st.title("Forecasting")
st.markdown(
    "The forecast supplies the expected baseline that variance analysis compares "
    "against. It is an input to evidence, not a prediction product in its own right."
)

metric = st.selectbox("Metric", list(views["accuracy"]), index=0)
accuracy = views["accuracy"][metric]
backtest = views["backtests"][metric]
best = accuracy.iloc[0]

left, right, third = st.columns(3)
left.metric("Best model", best["model"])
right.metric("WAPE", f"{best['wape']:.4f}")
third.metric(
    "Against naive baseline",
    f"{best['wape_improvement_vs_baseline']:+.1%}" if best["model"] != "naive" else "baseline",
)
caveat(
    f"Rolling-origin backtest: horizon {settings['horizon']} days, "
    f"initial training window {settings['initial_train']} days, step {settings['step']}. "
    f"Each model is refitted at every origin on data strictly before the window it is scored on."
)

st.divider()
st.subheader("Model comparison")
st.plotly_chart(
    comparison_bars(accuracy, "model", "wape", title="WAPE, lower is better"),
    use_container_width=True,
)
st.dataframe(
    accuracy[["model", "folds", "n_observations", "mae", "rmse", "wape", "mape", "bias"]],
    hide_index=True,
    use_container_width=True,
)
caveat(
    "MAPE is blank where a near-zero actual would make it meaningless. WAPE is the "
    "metric to read. A model below the baseline is reported as such rather than hidden."
)

st.divider()
st.subheader(f"Actual against forecast — {best['model']}")
st.plotly_chart(
    actual_versus_expected(backtest, "date", "actual", "forecast", title=""),
    use_container_width=True,
)
st.plotly_chart(
    trend_line(backtest, "date", "error", title="Forecast error (forecast minus actual)"),
    use_container_width=True,
)
caveat(
    "These are out-of-sample values from the backtest folds, not an in-sample fit. "
    "Accuracy is measured at a daily company-wide grain; segment-level forecasts have "
    "not been evaluated and should not be assumed to reach the same accuracy."
)

st.divider()
with st.expander("The daily series being forecast"):
    st.dataframe(views["series"].reset_index(), hide_index=True, use_container_width=True)
