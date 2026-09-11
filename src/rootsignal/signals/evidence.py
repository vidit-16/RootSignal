"""Gather the operational evidence that surrounds a metric movement.

A movement in one metric says very little on its own. Fill rate falling could
mean fulfilment broke down, or that demand moved toward products that were never
well stocked, or simply that a thin segment had a noisy week. What separates
those readings is what the *other* metrics did at the same time.

This module collects that surrounding evidence and describes it. It labels a
pattern as consistent with an explanation; it never asserts that the explanation
is the cause.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from ..analysis.periods import PERIOD_COLUMN, add_period_column
from ..modeling.consolidation import _left_enrich

# A change smaller than this is treated as the metric holding its level rather
# than moving. Weekly figures in a thin segment wander by a few percent without
# anything having happened.
STABLE_BAND = 0.05

# Inventory levels are snapshots and inventory receipts are flows, so they
# aggregate differently. Averaging a flow or summing a level would both be wrong.
INVENTORY_LEVEL_COLUMNS = ("opening_stock", "available_stock")
INVENTORY_FLOW_COLUMNS = ("received_units", "ordered_units", "fulfilled_units")


@dataclass(frozen=True)
class Observation:
    """One metric's movement between two periods."""

    name: str
    before: float
    after: float
    change: float
    change_pct: float | None
    direction: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "before": round(self.before, 4),
            "after": round(self.after, 4),
            "change": round(self.change, 4),
            "change_pct": None if self.change_pct is None else round(self.change_pct, 4),
            "direction": self.direction,
        }


def _observe(name: str, before: float, after: float, stable_band: float = STABLE_BAND) -> Observation:
    before, after = float(before), float(after)
    change = after - before
    change_pct = change / abs(before) if before != 0 else None

    if change_pct is None:
        direction = "up" if change > 0 else ("down" if change < 0 else "flat")
    elif abs(change_pct) < stable_band:
        direction = "flat"
    else:
        direction = "up" if change_pct > 0 else "down"
    return Observation(name, before, after, change, change_pct, direction)


def summarise_inventory_by_period(
    tables: dict[str, pd.DataFrame],
    period: str = "week",
    group_by: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Aggregate inventory snapshots to a calendar period.

    Stock levels are averaged and stock movements are summed, because a level
    that exists on every day of a week is not seven times larger than itself.
    ``stockout_rate`` is the share of SKU-days flagged as out of stock.

    Inventory is kept out of the commercial mart because it sits at a different
    grain, SKU by warehouse by day, and joining it to order lines would multiply
    rows.
    """
    groups = list(group_by or [])
    inventory = tables["fact_inventory"].copy()

    needs_sku_attributes = any(
        column in groups for column in ("category", "sub_category", "sku_name")
    )
    if needs_sku_attributes:
        inventory = _left_enrich(
            inventory,
            tables["dim_sku"],
            "sku_id",
            ["sku_name", "category", "sub_category"],
            "dim_sku",
        )

    missing = [column for column in groups if column not in inventory.columns]
    if missing:
        raise ValueError(f"Inventory cannot be grouped by {missing}.")

    required = (*INVENTORY_LEVEL_COLUMNS, *INVENTORY_FLOW_COLUMNS, "stockout_flag")
    absent = [column for column in required if column not in inventory.columns]
    if absent:
        raise ValueError(f"fact_inventory is missing required columns: {absent}")

    inventory = add_period_column(inventory, period)
    grain = [PERIOD_COLUMN, *groups]

    aggregations = {column: "mean" for column in INVENTORY_LEVEL_COLUMNS}
    aggregations.update({column: "sum" for column in INVENTORY_FLOW_COLUMNS})
    aggregations["stockout_flag"] = "mean"

    summary = inventory.groupby(grain, dropna=False, as_index=False).agg(aggregations)
    return summary.rename(columns={"stockout_flag": "stockout_rate"}).sort_values(
        grain, ignore_index=True
    )


def _segment_rows(summary: pd.DataFrame, dimension: Sequence[str], segment: Sequence[str]) -> pd.DataFrame:
    mask = pd.Series(True, index=summary.index)
    for column, value in zip(list(dimension), list(segment)):
        mask &= summary[column].astype(str) == str(value)
    return summary.loc[mask]


def _value_at(summary: pd.DataFrame, column: str, period: pd.Timestamp) -> float:
    if column not in summary.columns:
        return float("nan")
    rows = summary.loc[pd.to_datetime(summary[PERIOD_COLUMN]) == period, column]
    return float(rows.iloc[0]) if len(rows) else 0.0


def gather_segment_evidence(
    commercial_summary: pd.DataFrame,
    dimension: Sequence[str],
    segment: Sequence[str],
    current_period: pd.Timestamp,
    comparison_period: pd.Timestamp,
    inventory_summary: pd.DataFrame | None = None,
) -> dict[str, dict]:
    """Collect how a segment's related metrics moved over the same two periods.

    Inventory evidence is included when an inventory summary is supplied at a
    matching grain, and simply absent otherwise. An absent observation is left
    out rather than defaulted, so a reader can tell the difference between
    evidence that pointed nowhere and evidence that was never available.
    """
    current_period = pd.Timestamp(current_period)
    comparison_period = pd.Timestamp(comparison_period)
    rows = _segment_rows(commercial_summary, dimension, segment)

    observations: dict[str, Observation] = {}
    for column in ("ordered_units", "fulfilled_units", "cancelled_units", "net_sales", "order_count"):
        if column in rows.columns:
            observations[column] = _observe(
                column,
                _value_at(rows, column, comparison_period),
                _value_at(rows, column, current_period),
            )

    for rate in ("fill_rate", "cancellation_rate"):
        if rate in rows.columns:
            observations[rate] = _observe(
                rate,
                _value_at(rows, rate, comparison_period),
                _value_at(rows, rate, current_period),
            )

    if inventory_summary is not None:
        inventory_rows = _segment_rows(inventory_summary, dimension, segment)
        for column in ("available_stock", "stockout_rate", "received_units"):
            if column in inventory_rows.columns:
                observations[column] = _observe(
                    column,
                    _value_at(inventory_rows, column, comparison_period),
                    _value_at(inventory_rows, column, current_period),
                )

    return {name: observation.as_dict() for name, observation in observations.items()}


def periods_of_consistent_movement(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    segment: Sequence[str],
    current_period: pd.Timestamp,
) -> int:
    """Count consecutive periods, ending at the current one, moving the same way.

    A movement repeated across several periods is harder to dismiss as noise
    than a single week. This counts persistence; it does not test significance.
    """
    rows = _segment_rows(summary, dimension, segment).copy()
    if metric not in rows.columns or rows.empty:
        return 0

    rows[PERIOD_COLUMN] = pd.to_datetime(rows[PERIOD_COLUMN])
    rows = rows.loc[rows[PERIOD_COLUMN] <= pd.Timestamp(current_period)].sort_values(PERIOD_COLUMN)
    values = rows[metric].to_numpy(dtype=float)
    if len(values) < 2:
        return 0

    changes = values[1:] - values[:-1]
    latest = changes[-1]
    if latest == 0:
        return 0

    streak = 0
    for change in reversed(changes):
        if (change > 0) == (latest > 0) and change != 0:
            streak += 1
        else:
            break
    return streak
