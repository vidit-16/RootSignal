"""Variance analysis against prior periods, targets, and forecasts.

All three comparisons emit the same record shape. That uniformity is the point:
driver decomposition and the signal layer downstream should be able to consume
"this metric is below expectation" without caring whether the expectation came
from last week, from a plan, or from a model.

Every record states what it was compared against, so a reader is never left
guessing whether a gap is against target or against history.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .periods import PERIOD_COLUMN, add_period_column
from .trends import calculate_trend

VARIANCE_COLUMNS = [
    "metric",
    "segment",
    "period_type",
    PERIOD_COLUMN,
    "comparison_period",
    "actual",
    "comparison_value",
    "variance",
    "variance_pct",
    "comparison_type",
]

COMPARISON_PREVIOUS_PERIOD = "previous_period"
COMPARISON_TARGET = "target"
COMPARISON_FORECAST = "forecast"

# Rate metrics are not quantities and must never be summed across rows.
RATIO_METRICS = frozenset({"fill_rate", "cancellation_rate", "aov", "primary_mix", "secondary_mix"})

# How each target column combines when several target rows fall inside one
# period. Quantities add up; a rate target is averaged across the days it
# covers, because the demand weights that would justify anything better are not
# present in fact_targets. The assumption is stated wherever the number is used.
TARGET_AGGREGATIONS = {
    "sales_target": "sum",
    "order_target": "sum",
    "fill_rate_target": "mean",
}


def _segment_label(frame: pd.DataFrame, groups: Sequence[str]) -> pd.Series:
    """Render the grouping columns as one readable segment name."""
    if not groups:
        return pd.Series("TOTAL", index=frame.index, dtype="object")
    return frame[list(groups)].astype(str).agg(" | ".join, axis=1)


def _variance_pct(variance: pd.Series, comparison: pd.Series) -> pd.Series:
    """Variance as a share of the comparison value, undefined against zero."""
    return pd.Series(
        np.where(comparison.abs() > 0, variance / comparison, np.nan),
        index=variance.index,
    ).round(4)


def _assemble(
    frame: pd.DataFrame,
    metric: str,
    groups: Sequence[str],
    comparison_type: str,
) -> pd.DataFrame:
    """Complete a variance frame into the shared schema.

    Expects actual, comparison_value, period_start and comparison_period to be
    present already.
    """
    result = frame.copy()
    result["metric"] = metric
    result["segment"] = _segment_label(result, groups)
    result["comparison_type"] = comparison_type
    result["variance"] = result["actual"] - result["comparison_value"]
    result["variance_pct"] = _variance_pct(result["variance"], result["comparison_value"])
    if "period_type" not in result.columns:
        result["period_type"] = pd.NA

    columns = VARIANCE_COLUMNS + [group for group in groups if group not in VARIANCE_COLUMNS]
    return result[columns].reset_index(drop=True)


def variance_vs_previous_period(
    period_summary: pd.DataFrame,
    metric: str,
    group_by: Sequence[str] | None = None,
    periods_back: int = 1,
) -> pd.DataFrame:
    """Compare each period against an earlier period in the same segment."""
    groups = list(group_by or [])
    trend = calculate_trend(period_summary, metric, groups, periods_back=periods_back)
    if trend.empty:
        return _assemble(
            trend.assign(actual=[], comparison_value=[], comparison_period=[]),
            metric,
            groups,
            COMPARISON_PREVIOUS_PERIOD,
        )

    frame = trend.rename(
        columns={
            "value": "actual",
            "previous_value": "comparison_value",
            "previous_period_start": "comparison_period",
        }
    )
    return _assemble(frame, metric, groups, COMPARISON_PREVIOUS_PERIOD)


def variance_vs_target(
    period_summary: pd.DataFrame,
    targets: pd.DataFrame,
    metric: str,
    target_column: str,
    period: str = "day",
    group_by: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Compare actuals against plan at a matching period and segment grain.

    Targets are aggregated to the same grain as the actuals before merging, so
    no fact is joined to another fact at an incompatible grain. Quantity targets
    are summed; rate targets are averaged across the period, which is an
    assumption rather than a derivation and is recorded in docs/analysis.md.
    """
    groups = list(group_by or [])
    if metric not in period_summary.columns:
        raise ValueError(f"Period summary has no metric '{metric}'.")
    if target_column not in targets.columns:
        raise ValueError(f"Targets have no column '{target_column}'.")

    aggregation = TARGET_AGGREGATIONS.get(target_column)
    if aggregation is None:
        raise ValueError(
            f"No aggregation defined for target '{target_column}'; "
            f"known targets are {sorted(TARGET_AGGREGATIONS)}."
        )

    missing_dimensions = [group for group in groups if group not in targets.columns]
    if missing_dimensions:
        raise ValueError(
            f"Targets cannot be compared at this grain; missing dimensions: {missing_dimensions}."
        )

    target_periods = add_period_column(targets, period)
    grain = [PERIOD_COLUMN, *groups]
    target_agg = target_periods.groupby(grain, dropna=False, as_index=False).agg(
        comparison_value=(target_column, aggregation)
    )

    actual_columns = [*grain, metric] + (
        ["period_type"] if "period_type" in period_summary.columns else []
    )
    actual = period_summary[actual_columns].rename(columns={metric: "actual"})

    merged = actual.merge(target_agg, on=grain, how="left", validate="one_to_one")
    merged["comparison_period"] = merged[PERIOD_COLUMN]
    return _assemble(merged, metric, groups, COMPARISON_TARGET)


def variance_vs_forecast(
    actual: pd.Series,
    forecast: pd.Series,
    metric: str,
    segment: str = "TOTAL",
    period_type: str = "day",
) -> pd.DataFrame:
    """Compare realised values against a forecast over the same dates.

    Takes the date-indexed series the forecasting layer produces. Only dates
    present in both are compared; a forecast without an actual is not yet
    evidence of anything.

    Forecasts are currently evaluated at total daily grain, so segment-level
    forecast variance requires segment-level forecasts and should not be
    inferred from this comparison.
    """
    if not isinstance(actual.index, pd.DatetimeIndex):
        raise ValueError("Actual series must be indexed by date.")
    if not isinstance(forecast.index, pd.DatetimeIndex):
        raise ValueError("Forecast series must be indexed by date.")

    shared = actual.index.intersection(forecast.index)
    if len(shared) == 0:
        raise ValueError("Actual and forecast series share no dates.")

    frame = pd.DataFrame(
        {
            PERIOD_COLUMN: shared,
            "comparison_period": shared,
            "actual": actual.reindex(shared).to_numpy(dtype=float),
            "comparison_value": forecast.reindex(shared).to_numpy(dtype=float),
            "period_type": period_type,
            "segment": segment,
        }
    )
    assembled = _assemble(frame, metric, [], COMPARISON_FORECAST)
    assembled["segment"] = segment
    return assembled


def combine_variances(*frames: pd.DataFrame) -> pd.DataFrame:
    """Stack variance frames that were produced by different comparisons.

    Rejects frames that do not carry the shared schema, so a drifting producer
    fails here rather than silently contributing malformed evidence.
    """
    supplied = [frame for frame in frames if frame is not None]
    if not supplied:
        return pd.DataFrame(columns=VARIANCE_COLUMNS)

    for position, frame in enumerate(supplied):
        missing = [column for column in VARIANCE_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"Variance frame {position} is missing columns: {missing}")

    return pd.concat(supplied, ignore_index=True, sort=False)


def rank_variances(
    variances: pd.DataFrame,
    direction: str = "negative",
    top_n: int = 10,
) -> pd.DataFrame:
    """Order variance records by the size of the gap.

    This ranks by magnitude only. It says which gaps are largest, not which are
    most important and not what caused them; attribution is the job of driver
    decomposition.
    """
    if direction not in {"negative", "positive", "absolute"}:
        raise ValueError("direction must be 'negative', 'positive' or 'absolute'.")
    if variances.empty:
        return variances

    frame = variances.copy()
    if direction == "negative":
        frame = frame[frame["variance"] < 0].sort_values("variance")
    elif direction == "positive":
        frame = frame[frame["variance"] > 0].sort_values("variance", ascending=False)
    else:
        frame = frame.reindex(frame["variance"].abs().sort_values(ascending=False).index)

    return frame.head(top_n).reset_index(drop=True)
