"""Shared loading, caching and layout for the dashboard pages.

Everything analytical lives in `rootsignal.dashboard`, which imports no
Streamlit and is tested without one. This module only caches those functions and
draws the chrome, so a page contains layout rather than analysis.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from rootsignal.dashboard import (  # noqa: E402
    DEFAULT_INPUT_DIR,
    available_periods,
    forecast_views,
    load_business_data,
    overview,
    sales_views,
    signal_views,
    supply_views,
)

PERIODS = ("day", "week", "month")


@st.cache_data(show_spinner="Loading and cleaning the dataset...")
def get_data(input_dir: str = DEFAULT_INPUT_DIR) -> dict:
    return load_business_data(input_dir)


@st.cache_data(show_spinner=False)
def get_overview(input_dir: str, period: str) -> dict:
    return overview(get_data(input_dir)["tables"], period=period)


@st.cache_data(show_spinner=False)
def get_sales(input_dir: str, period: str) -> dict:
    return sales_views(get_data(input_dir)["tables"], period=period)


@st.cache_data(show_spinner=False)
def get_supply(input_dir: str, period: str) -> dict:
    return supply_views(get_data(input_dir)["tables"], period=period)


@st.cache_data(show_spinner="Backtesting forecast models...")
def get_forecasts(input_dir: str) -> dict:
    return forecast_views(get_data(input_dir)["tables"])


@st.cache_data(show_spinner="Assembling signals...")
def get_signals(
    input_dir: str,
    metric: str,
    dimension: tuple[str, ...],
    current: str | None,
    comparison: str | None,
    min_confidence: str | None,
) -> dict:
    return signal_views(
        get_data(input_dir)["tables"],
        metric=metric,
        dimension=list(dimension),
        current_period=current,
        comparison_period=comparison,
        min_confidence=min_confidence,
    )


@st.cache_data(show_spinner=False)
def get_periods(input_dir: str, period: str) -> list:
    return available_periods(get_data(input_dir)["tables"], period=period)


def configure(title: str) -> None:
    st.set_page_config(page_title=f"RootSignal - {title}", page_icon="🌱", layout="wide")


def sidebar(show_period: bool = True) -> tuple[str, str]:
    """Draw the shared controls and return the selected data source and period."""
    with st.sidebar:
        st.markdown("### RootSignal")
        st.caption("Evidence-backed analytics for sales, supply and business performance.")
        input_dir = st.text_input("Dataset directory", value=DEFAULT_INPUT_DIR)
        period = (
            st.selectbox("Period", PERIODS, index=1, help="Partial periods are excluded.")
            if show_period
            else "week"
        )
        if st.button("Reload data", use_container_width=True):
            # Cache keys are built from these wrappers' arguments, so a change to
            # the underlying analytics or to the files on disk does not invalidate
            # them on its own. This is the way to pick such a change up.
            st.cache_data.clear()
            st.rerun()

        st.divider()
        data = get_data(input_dir)
        audit = data["audit"]
        st.caption(
            f"{len(data['tables'])} tables loaded. "
            f"Cleaning made {len(audit)} recorded change(s); "
            f"{sum(len(f) for f in data['quarantined'].values())} row(s) quarantined."
        )
    return input_dir, period


def metric_row(headline: dict, keys: list[tuple[str, str, str]]) -> None:
    """Render headline figures with their movement against the prior period."""
    columns = st.columns(len(keys))
    for column, (key, label, fmt) in zip(columns, keys):
        entry = headline.get(key)
        if entry is None:
            column.metric(label, "n/a")
            continue
        value = entry["value"]
        change = entry["change_pct"]
        column.metric(
            label,
            fmt.format(value) if pd.notna(value) else "n/a",
            f"{change:+.1%}" if change is not None else None,
        )


def caveat(text: str) -> None:
    st.caption(f":grey[{text}]")
