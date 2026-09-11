"""Calendar period handling for trend and variance analysis.

Period comparisons are only meaningful between periods of equal length. The
sample data starts on a Thursday and ends on the first of a month, so the first
week holds four days and the last month holds one. Comparing those against full
periods would report a 75% surge into week two and a 96% collapse into March,
neither of which happened.

Every period is therefore labelled complete or partial, and partial periods are
excluded from comparisons by default.
"""

from __future__ import annotations

import pandas as pd

PERIOD_FREQUENCIES = {"day": "D", "week": "W-SUN", "month": "M"}
PERIOD_COLUMN = "period_start"


def _validate_period(period: str) -> str:
    if period not in PERIOD_FREQUENCIES:
        raise ValueError(
            f"Unsupported period '{period}'; expected one of {sorted(PERIOD_FREQUENCIES)}."
        )
    return PERIOD_FREQUENCIES[period]


def add_period_column(
    frame: pd.DataFrame,
    period: str = "day",
    date_column: str = "date",
    column: str = PERIOD_COLUMN,
) -> pd.DataFrame:
    """Label every row with the start date of the period it belongs to.

    Weeks run Monday to Sunday, so a week is identified by its Monday.
    """
    frequency = _validate_period(period)
    if date_column not in frame.columns:
        raise ValueError(f"Frame is missing the date column '{date_column}'.")

    result = frame.copy()
    dates = pd.to_datetime(result[date_column], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"Column '{date_column}' contains values that are not dates.")
    result[column] = dates.dt.to_period(frequency).dt.start_time
    return result


def period_calendar(period: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Describe each period spanned by a date range, and whether it is complete.

    A period is complete when the whole of it falls inside the observed range.
    Completeness is judged against the calendar rather than against the number
    of rows present, so a genuine zero-trade day never makes a period look
    partial.
    """
    frequency = _validate_period(period)
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if start > end:
        raise ValueError("Range start must not be after range end.")

    periods = pd.period_range(start=start, end=end, freq=frequency)
    calendar = pd.DataFrame(
        {
            PERIOD_COLUMN: periods.start_time,
            "period_end": periods.end_time.normalize(),
        }
    )
    calendar["period_type"] = period
    calendar["expected_days"] = (
        (calendar["period_end"] - calendar[PERIOD_COLUMN]).dt.days + 1
    ).astype(int)
    calendar["is_complete"] = (calendar[PERIOD_COLUMN] >= start) & (
        calendar["period_end"] <= end
    )
    return calendar


def attach_period_completeness(
    frame: pd.DataFrame,
    period: str,
    observed_start: pd.Timestamp,
    observed_end: pd.Timestamp,
    column: str = PERIOD_COLUMN,
) -> pd.DataFrame:
    """Join period metadata onto an already-aggregated frame."""
    if column not in frame.columns:
        raise ValueError(f"Frame is missing the period column '{column}'.")

    calendar = period_calendar(period, observed_start, observed_end)
    result = frame.copy()
    result[column] = pd.to_datetime(result[column])
    return result.merge(calendar, on=column, how="left", validate="many_to_one")


def drop_partial_periods(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only complete periods, preserving column order."""
    if "is_complete" not in frame.columns:
        raise ValueError("Frame has no 'is_complete' column; attach period metadata first.")
    return frame.loc[frame["is_complete"]].reset_index(drop=True)
