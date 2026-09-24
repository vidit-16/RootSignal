"""The analyses the warehouse serves alongside the model.

The fact and dimension tables let a BI tool slice revenue, but the findings this
project exists for, which segments moved a metric and whether a rate moved
because segments changed or because the mix did, come from the Python layers.
These are computed here once, with the same calls and parameters as
scripts/run_external_dataset.py, and written to the warehouse as report tables,
so a dashboard reads the project's results rather than re-deriving them.
"""

from __future__ import annotations

import pandas as pd

from ..adapters.online_retail import returns_by_period
from ..analysis import calculate_trend, summarise_by_period
from ..decomposition import component_coherence, decompose_additive, decompose_movement
from ..forecasting import (
    build_daily_series,
    compare_against_baseline,
    extract_metric,
    rolling_origin_evaluate,
    summarise_backtest,
)


def analysis_tables(tables: dict[str, pd.DataFrame], returns: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every report table, keyed by the name it is published under."""
    sales = tables["fact_sales"]

    monthly = summarise_by_period(tables, period="month")
    trend = calculate_trend(monthly, "net_sales")

    by_country = summarise_by_period(tables, period="month", group_by=["region_code"])
    contribution = decompose_additive(by_country, "net_sales", ["region_code"])

    # Countries with almost no volume swing between extremes; min_share folds
    # them into one bucket rather than dropping volume, as the script does.
    returns_monthly = returns_by_period(sales, returns, period="month", group_by=["region_code"])
    return_split = decompose_movement(returns_monthly, "return_rate", ["region_code"], min_share=0.01)

    daily = build_daily_series(sales, metrics=["net_sales", "units"])
    accuracy = compare_against_baseline(
        summarise_backtest(
            rolling_origin_evaluate(extract_metric(daily, "net_sales"), horizon=7, initial_train=56, step=14)
        )
    )

    return {
        "rpt_monthly_trade": monthly,
        "rpt_monthly_trend": trend,
        "rpt_country_contribution": contribution,
        "rpt_returns_monthly": returns_monthly,
        "rpt_return_rate_split": return_split,
        "rpt_return_rate_components": component_coherence(return_split),
        "rpt_forecast_accuracy": accuracy,
    }


def flatten(frame: pd.DataFrame) -> pd.DataFrame:
    """A frame a SQL table can hold: plain columns, no index, no Python objects.

    Periods and timestamps become dates, and anything else that is not a
    number, a boolean or a date is written as text.
    """
    out = frame.reset_index(drop=True).copy()
    out.columns = [str(column) for column in out.columns]
    for column in out.columns:
        series = out[column]
        if isinstance(series.dtype, pd.PeriodDtype):
            out[column] = series.dt.start_time.dt.date
        elif pd.api.types.is_datetime64_any_dtype(series):
            out[column] = series.dt.date
        elif pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
            continue
        elif series.map(lambda value: value is None or hasattr(value, "isoformat")).all():
            continue
        else:
            out[column] = series.map(lambda value: None if value is None or value != value else str(value))
    return out
