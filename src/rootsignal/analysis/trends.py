"""Trend analysis: how a metric moved from one period to the next.

The point of this layer is to make the comparison explicit. A chart can suggest
that sales fell; a trend record states which metric, over which period, against
which prior period, by how much in absolute terms, and by what percentage.

Metrics are recomputed at the target period grain rather than rolled up from
daily values, so ratios such as fill rate stay volume-weighted.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..modeling.consolidation import aggregate_commercial_metrics, enrich_orders, enrich_sales
from .periods import (
    PERIOD_COLUMN,
    add_period_column,
    attach_period_completeness,
    drop_partial_periods,
)

# Metrics that may be compared across periods. Ratios are included because
# aggregate_commercial_metrics recomputes them from summed components at the
# requested grain rather than averaging a finer-grained ratio.
TREND_METRICS = (
    "net_sales",
    "gross_sales",
    "sales_units",
    "sales_order_count",
    "order_count",
    "ordered_units",
    "fulfilled_units",
    "cancelled_units",
    "discount_value",
    "fill_rate",
    "cancellation_rate",
    "aov",
)


def summarise_by_period(
    tables: dict[str, pd.DataFrame],
    period: str = "day",
    group_by: Sequence[str] | None = None,
    include_partial_periods: bool = False,
) -> pd.DataFrame:
    """Aggregate cleaned facts to a calendar period, optionally within segments.

    Sales and order facts are aggregated independently and merged one-to-one, so
    a multi-SKU order is never counted more than once.

    Partial periods are dropped by default. The first and last period of a range
    are frequently incomplete, and comparing a four-day week against a seven-day
    week reports a change in calendar coverage as though it were a change in
    business performance.
    """
    groups = list(group_by or [])
    sales = add_period_column(enrich_sales(tables), period)
    orders = add_period_column(enrich_orders(tables), period)

    grain = [PERIOD_COLUMN, *groups]
    summary = aggregate_commercial_metrics(sales, orders, grain)

    observed = pd.to_datetime(
        pd.concat([sales["date"], orders["date"]], ignore_index=True), errors="coerce"
    )
    summary = attach_period_completeness(summary, period, observed.min(), observed.max())
    if not include_partial_periods:
        summary = drop_partial_periods(summary)
    return summary.sort_values(grain).reset_index(drop=True)


def calculate_trend(
    period_summary: pd.DataFrame,
    metric: str,
    group_by: Sequence[str] | None = None,
    periods_back: int = 1,
) -> pd.DataFrame:
    """Compare each period against an earlier one within the same segment.

    Returns one row per comparison, carrying both the absolute movement and the
    percentage movement. Percentage movement is undefined when the prior value
    is zero, and is reported as NaN rather than as an infinite change.
    """
    if metric not in period_summary.columns:
        raise ValueError(f"Period summary has no metric '{metric}'.")
    if PERIOD_COLUMN not in period_summary.columns:
        raise ValueError(f"Period summary is missing '{PERIOD_COLUMN}'.")
    if periods_back < 1:
        raise ValueError("periods_back must be at least 1.")

    groups = list(group_by or [])
    if PERIOD_COLUMN in groups:
        raise ValueError(f"'{PERIOD_COLUMN}' must not appear in group_by.")

    frame = period_summary.copy()
    frame[PERIOD_COLUMN] = pd.to_datetime(frame[PERIOD_COLUMN])
    frame = frame.sort_values([*groups, PERIOD_COLUMN]).reset_index(drop=True)

    shifted = frame.groupby(groups, dropna=False) if groups else frame
    frame["previous_period_start"] = (
        shifted[PERIOD_COLUMN].shift(periods_back)
        if groups
        else frame[PERIOD_COLUMN].shift(periods_back)
    )
    frame["previous_value"] = (
        shifted[metric].shift(periods_back) if groups else frame[metric].shift(periods_back)
    )

    frame["metric"] = metric
    frame["value"] = frame[metric]
    frame["absolute_change"] = frame["value"] - frame["previous_value"]
    frame["pct_change"] = np.where(
        frame["previous_value"].abs() > 0,
        frame["absolute_change"] / frame["previous_value"],
        np.nan,
    )
    frame["pct_change"] = frame["pct_change"].round(4)

    columns = [
        "metric",
        *groups,
        "period_type",
        PERIOD_COLUMN,
        "previous_period_start",
        "value",
        "previous_value",
        "absolute_change",
        "pct_change",
    ]
    available = [column for column in columns if column in frame.columns]
    return frame.loc[frame["previous_value"].notna(), available].reset_index(drop=True)


def latest_movement(trend: pd.DataFrame, group_by: Sequence[str] | None = None) -> pd.DataFrame:
    """Return only the most recent comparison per segment."""
    if trend.empty:
        return trend
    groups = list(group_by or [])
    ordered = trend.sort_values([*groups, PERIOD_COLUMN])
    if not groups:
        return ordered.tail(1).reset_index(drop=True)
    return ordered.groupby(groups, dropna=False, as_index=False).tail(1).reset_index(drop=True)
