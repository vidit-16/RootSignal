"""Attribute a metric movement to the segments that produced it.

Two different decompositions live here because two different kinds of metric
behave differently under aggregation.

An additive metric such as net sales is the sum of its segments, so a movement
splits cleanly: each segment contributes its own change, and the contributions
add back to the total.

A rate such as fill rate is not a sum. Total fill rate can fall while every
single segment's fill rate improves, purely because demand shifted toward
segments that fill less well. Subtracting segment rates would attribute that
movement to the wrong place entirely, so rates are decomposed into a rate
effect, a mix effect and an interaction term that add back exactly.

Both decompositions are exact rather than approximate: contributions reconstruct
the observed movement, and a test asserts it.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..analysis.periods import PERIOD_COLUMN

# Metrics that are sums over their segments.
ADDITIVE_METRICS = (
    "net_sales",
    "gross_sales",
    "sales_units",
    "ordered_units",
    "fulfilled_units",
    "cancelled_units",
    "order_count",
    "sales_order_count",
    "discount_value",
)

# Rates, mapped to the components they are computed from. A rate must be
# rebuilt from summed components at every level, never averaged.
RATE_COMPONENTS = {
    "fill_rate": ("fulfilled_units", "ordered_units"),
    "cancellation_rate": ("cancelled_units", "ordered_units"),
    "aov": ("net_sales", "sales_order_count"),
}

# Below this ratio of net movement to gross movement, segment shares stop being
# meaningful: the segments largely cancel out, so dividing by the small residual
# produces enormous percentages that describe arithmetic rather than business.
OFFSETTING_RATIO = 0.05

CONTRIBUTION_COLUMNS = [
    "metric",
    "dimension",
    "segment",
    PERIOD_COLUMN,
    "comparison_period",
    "contribution",
    "contribution_share",
    "share_of_absolute_movement",
]


def _segment_label(frame: pd.DataFrame, dimension: Sequence[str]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="object")
    return frame[list(dimension)].astype(str).agg(" | ".join, axis=1)


def _resolve_periods(
    summary: pd.DataFrame,
    current_period: object | None,
    comparison_period: object | None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Default to the latest period and the one immediately before it."""
    if PERIOD_COLUMN not in summary.columns:
        raise ValueError(f"Summary is missing '{PERIOD_COLUMN}'.")

    periods = pd.Index(sorted(pd.to_datetime(summary[PERIOD_COLUMN]).unique()))
    if len(periods) < 2 and (current_period is None or comparison_period is None):
        raise ValueError("At least two periods are needed to decompose a movement.")

    current = pd.Timestamp(current_period) if current_period is not None else periods[-1]
    if comparison_period is not None:
        comparison = pd.Timestamp(comparison_period)
    else:
        earlier = periods[periods < current]
        if len(earlier) == 0:
            raise ValueError(f"No period precedes {current.date()} in this summary.")
        comparison = earlier[-1]

    for label, period in (("current", current), ("comparison", comparison)):
        if period not in periods:
            raise ValueError(f"The {label} period {period.date()} is not present in the summary.")
    if comparison >= current:
        raise ValueError("The comparison period must precede the current period.")
    return current, comparison


def _period_slice(summary: pd.DataFrame, period: pd.Timestamp, dimension: list[str]) -> pd.DataFrame:
    frame = summary.copy()
    frame[PERIOD_COLUMN] = pd.to_datetime(frame[PERIOD_COLUMN])
    return frame.loc[frame[PERIOD_COLUMN] == period].set_index(dimension)


def _finalise(
    frame: pd.DataFrame,
    metric: str,
    dimension: list[str],
    current: pd.Timestamp,
    comparison: pd.Timestamp,
) -> pd.DataFrame:
    """Add shares and identity columns to a frame that already has contributions."""
    total_movement = float(frame["contribution"].sum())
    gross_movement = float(frame["contribution"].abs().sum())

    frame = frame.reset_index()
    frame["metric"] = metric
    frame["dimension"] = " | ".join(dimension)
    frame["segment"] = _segment_label(frame, dimension)
    frame[PERIOD_COLUMN] = current
    frame["comparison_period"] = comparison

    # Share of the net movement. Undefined when the segments largely offset,
    # because the denominator is then a small residual of large opposing moves.
    offsetting = gross_movement > 0 and abs(total_movement) < OFFSETTING_RATIO * gross_movement
    if total_movement == 0.0 or offsetting:
        frame["contribution_share"] = np.nan
    else:
        frame["contribution_share"] = (frame["contribution"] / total_movement).round(4)

    # Always well defined, so ranking by magnitude survives an offsetting period.
    frame["share_of_absolute_movement"] = (
        (frame["contribution"].abs() / gross_movement).round(4) if gross_movement > 0 else np.nan
    )

    extra = [column for column in frame.columns if column not in CONTRIBUTION_COLUMNS]
    ordered = frame[CONTRIBUTION_COLUMNS + extra]
    return ordered.sort_values("contribution").reset_index(drop=True)


def decompose_additive(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    current_period: object | None = None,
    comparison_period: object | None = None,
) -> pd.DataFrame:
    """Split an additive metric's movement across segments.

    Segments absent from one period are treated as zero there, so a segment that
    appeared or disappeared contributes its full value rather than being dropped
    from the reconciliation.
    """
    if metric not in ADDITIVE_METRICS:
        raise ValueError(
            f"'{metric}' is not an additive metric. Rates must use decompose_rate; "
            f"additive metrics are {sorted(ADDITIVE_METRICS)}."
        )
    dims = list(dimension)
    if metric not in summary.columns:
        raise ValueError(f"Summary has no metric '{metric}'.")
    missing = [column for column in dims if column not in summary.columns]
    if missing:
        raise ValueError(f"Summary is missing dimension columns: {missing}")

    current, comparison = _resolve_periods(summary, current_period, comparison_period)
    current_values = _period_slice(summary, current, dims)[metric]
    previous_values = _period_slice(summary, comparison, dims)[metric]

    frame = pd.DataFrame(
        {
            "current_value": current_values,
            "previous_value": previous_values,
        }
    ).fillna(0.0)
    frame["contribution"] = frame["current_value"] - frame["previous_value"]
    return _finalise(frame, metric, dims, current, comparison)


def decompose_rate(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    current_period: object | None = None,
    comparison_period: object | None = None,
) -> pd.DataFrame:
    """Split a rate movement into rate, mix and interaction effects.

    With weight w (a segment's share of the denominator) and rate r:

        total rate = sum(w * r)

    so the movement for each segment separates into

        rate effect  = w_before * (r_after - r_before)
        mix effect   = (w_after - w_before) * r_before
        interaction  = (w_after - w_before) * (r_after - r_before)

    which sum exactly to the observed change. The distinction is the point: a
    rate effect means a segment genuinely fulfils less of what it is asked for,
    while a mix effect means demand moved toward segments that always fulfilled
    less well. Those call for different responses.

    A segment with no volume in one period has no rate there; its rate is held
    equal to the other period's, so its entire movement lands in mix, which is
    what entering or leaving demand actually is.
    """
    if metric not in RATE_COMPONENTS:
        raise ValueError(
            f"'{metric}' is not a known rate. Known rates are {sorted(RATE_COMPONENTS)}."
        )
    numerator, denominator = RATE_COMPONENTS[metric]
    dims = list(dimension)
    for column in (numerator, denominator, *dims):
        if column not in summary.columns:
            raise ValueError(f"Summary is missing required column '{column}'.")

    current, comparison = _resolve_periods(summary, current_period, comparison_period)
    after = _period_slice(summary, current, dims)[[numerator, denominator]]
    before = _period_slice(summary, comparison, dims)[[numerator, denominator]]

    frame = pd.DataFrame(
        {
            "numerator_after": after[numerator],
            "denominator_after": after[denominator],
            "numerator_before": before[numerator],
            "denominator_before": before[denominator],
        }
    ).fillna(0.0)

    total_after = float(frame["denominator_after"].sum())
    total_before = float(frame["denominator_before"].sum())
    if total_after == 0.0 or total_before == 0.0:
        raise ValueError(
            f"Cannot decompose '{metric}': the denominator '{denominator}' is zero in a period."
        )

    frame["weight_after"] = frame["denominator_after"] / total_after
    frame["weight_before"] = frame["denominator_before"] / total_before
    frame["rate_after"] = frame["numerator_after"].div(
        frame["denominator_after"].where(frame["denominator_after"] != 0)
    )
    frame["rate_before"] = frame["numerator_before"].div(
        frame["denominator_before"].where(frame["denominator_before"] != 0)
    )
    # A segment with no volume in one period inherits the other period's rate,
    # so its movement is attributed to mix rather than to a rate change.
    frame["rate_after"] = frame["rate_after"].fillna(frame["rate_before"]).fillna(0.0)
    frame["rate_before"] = frame["rate_before"].fillna(frame["rate_after"]).fillna(0.0)

    weight_change = frame["weight_after"] - frame["weight_before"]
    rate_change = frame["rate_after"] - frame["rate_before"]
    frame["rate_effect"] = frame["weight_before"] * rate_change
    frame["mix_effect"] = weight_change * frame["rate_before"]
    frame["interaction_effect"] = weight_change * rate_change
    frame["contribution"] = (
        frame["rate_effect"] + frame["mix_effect"] + frame["interaction_effect"]
    )
    return _finalise(frame, metric, dims, current, comparison)


def decompose_movement(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    current_period: object | None = None,
    comparison_period: object | None = None,
) -> pd.DataFrame:
    """Decompose a movement, choosing the method the metric requires."""
    if metric in RATE_COMPONENTS:
        return decompose_rate(summary, metric, dimension, current_period, comparison_period)
    return decompose_additive(summary, metric, dimension, current_period, comparison_period)


def total_movement(decomposition: pd.DataFrame) -> float:
    """The movement the contributions reconstruct."""
    return float(decomposition["contribution"].sum())
