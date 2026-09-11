"""Build the daily time series that the forecasting layer consumes.

Forecasting operates on one metric at a time at a single daily grain. Facts
are aggregated independently and only then assembled, so an order carrying
several SKUs is never counted more than once.
"""

from collections.abc import Sequence

import pandas as pd

FORECASTABLE_METRICS = ("net_sales", "units", "orders")


def _daily_index(frame: pd.DataFrame, column: str = "date") -> pd.Series:
    return pd.to_datetime(frame[column], errors="coerce")


def build_daily_series(
    sales: pd.DataFrame,
    orders: pd.DataFrame | None = None,
    metrics: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Aggregate cleaned facts into a gap-free daily frame indexed by date.

    ``net_sales`` and ``units`` come from fact_sales; ``orders`` is a distinct
    count of order_id, so multi-SKU orders contribute a single order. Missing
    calendar days are inserted as zero rather than skipped, because a day with
    no trade is a real observation and dropping it would silently shorten the
    seasonal cycle.
    """
    requested = list(metrics or FORECASTABLE_METRICS)
    unknown = [m for m in requested if m not in FORECASTABLE_METRICS]
    if unknown:
        raise ValueError(f"Unsupported forecast metrics: {unknown}")
    if "orders" in requested and orders is None:
        raise ValueError("fact_orders is required to build the 'orders' series")

    sales = sales.copy()
    sales["date"] = _daily_index(sales)
    daily = sales.groupby("date", as_index=False).agg(
        net_sales=("net_sales", "sum"),
        units=("units", "sum"),
    )

    if "orders" in requested:
        order_frame = orders.copy()
        order_frame["date"] = _daily_index(order_frame)
        counts = order_frame.groupby("date", as_index=False).agg(orders=("order_id", "nunique"))
        daily = daily.merge(counts, on="date", how="outer", validate="one_to_one")

    daily = daily.set_index("date").sort_index()
    full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full_range).fillna(0.0)
    daily.index.name = "date"
    return daily[requested]


def extract_metric(daily: pd.DataFrame, metric: str) -> pd.Series:
    """Return one metric as a named, daily-indexed series."""
    if metric not in daily.columns:
        raise ValueError(f"Series does not contain metric '{metric}'; have {list(daily.columns)}")
    series = daily[metric].astype(float)
    series.name = metric
    return series
