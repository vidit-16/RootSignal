"""Turn internal names into language a business reader already understands.

This is a display layer and nothing else. It renames things on their way out; it
never renames anything on the way in, and no analytical code imports it. A
translated frame is for reading, not for computing against.

The need is not cosmetic. A dashboard aimed at a key account manager that
labels them `K001` when the customer master holds "Aarav Mehta" is asking its
reader to know the schema. So is a driver called `fulfilment_constraint` and a
confidence criterion called `movement_stands_out`. The analysis is only useful
if the person who has to act on it can read it.

Internal names stay internal: `signal.pattern.pattern` is still
`fulfilment_constraint`, because downstream code and tests depend on a stable
token. Only what reaches a human changes.
"""

from __future__ import annotations

import pandas as pd

# --------------------------------------------------------------------------
# Column labels
# --------------------------------------------------------------------------

COLUMN_LABELS = {
    # Commercial
    "net_sales": "Net sales",
    "gross_sales": "Gross sales",
    "sales_units": "Units sold",
    "units": "Units",
    "sales_order_count": "Orders with sales",
    "order_count": "Orders",
    "orders": "Orders",
    "aov": "Average order value",
    "discount_value": "Discount given",
    "primary_sales": "Primary (sell-in)",
    "secondary_sales": "Secondary (sell-through)",
    "total_sales": "Total sales",
    "primary_mix": "Primary share",
    "secondary_mix": "Secondary share",
    # Fulfilment
    "ordered_units": "Units ordered",
    "fulfilled_units": "Units delivered",
    "cancelled_units": "Units cancelled",
    "unfulfilled_units": "Units not delivered",
    "fill_rate": "Fill rate",
    "prior_fill_rate": "Fill rate, previous",
    "fill_rate_target": "Fill rate target",
    "fill_rate_variance": "Fill rate vs target",
    "fill_rate_change": "Fill rate change",
    "cancellation_rate": "Cancellation rate",
    # Inventory
    "available_stock": "Available stock",
    "opening_stock": "Opening stock",
    "received_units": "Units received",
    "stockout_rate": "Stockout rate",
    # Dimensions
    "region_code": "Region",
    "city": "City",
    "zone": "Zone",
    "category": "Category",
    "sub_category": "Sub-category",
    "channel": "Channel",
    "kam_id": "Account manager",
    "kam_name": "Account manager",
    "sku_id": "SKU code",
    "sku_name": "Product",
    "customer_id": "Customer code",
    "customer_name": "Customer",
    "customer_type": "Customer type",
    "sales_type": "Sales type",
    "segment": "Segment",
    "dimension": "Cut by",
    "warehouse": "Warehouse",
    # Periods
    "period_start": "Period",
    "week_start": "Week",
    "date": "Date",
    "period_type": "Period type",
    "comparison_period": "Compared with",
    "previous_period_start": "Previous period",
    "days_observed": "Days in period",
    "is_complete": "Complete period",
    # Comparison and movement
    "actual": "Actual",
    "comparison_value": "Expected",
    "sales_target": "Sales target",
    "order_target": "Order target",
    "variance": "Gap",
    "variance_pct": "Gap %",
    "attainment": "Attainment",
    "target_variance": "Gap to target",
    "target_variance_pct": "Gap to target %",
    "value": "Value",
    "previous_value": "Previous",
    "absolute_change": "Change",
    "pct_change": "Change %",
    "change": "Change",
    "change_pct": "Change %",
    "movement": "Change",
    "day_over_day_change": "Change vs previous day",
    "day_over_day_pct": "Change vs previous day %",
    "prior_day_net_sales": "Net sales, previous day",
    "direction": "Direction",
    "before": "Before",
    "after": "After",
    # Decomposition
    "contribution": "Contribution",
    "contribution_share": "Share of the change",
    "share_of_absolute_movement": "Share of all movement",
    "share_of_net_movement": "Share of net movement",
    "rate_effect": "From fulfilment",
    "mix_effect": "From demand mix",
    "interaction_effect": "From both together",
    "coherence": "Consistency",
    "ranked_by": "Ranked by",
    # Signals
    "likely_driver": "Likely driver",
    "impact": "Estimated impact",
    "estimated_impact": "Estimated impact",
    "confidence": "Confidence",
    "priority_score": "Priority",
    "recommended_investigation": "What to check next",
    "observation": "Measure",
    "criterion": "Check",
    "met": "Passed",
    "detail": "Detail",
    "basis": "Based on",
    # Forecasting
    "model": "Method",
    "wape": "Error rate (WAPE)",
    "mae": "Average error",
    "rmse": "Error (RMSE)",
    "mape": "Error rate (MAPE)",
    "bias": "Bias",
    "folds": "Test rounds",
    "n_observations": "Days scored",
    "wape_improvement_vs_baseline": "Better than baseline",
    "forecast": "Forecast",
    "error": "Error",
    "fold": "Round",
    "origin_date": "Forecast made after",
    "baseline": "Baseline",
}

# A method name a reader can say out loud.
MODEL_LABELS = {
    "naive": "Repeat yesterday",
    "moving_average_7": "7-day average",
    "ses": "Weighted recent average",
    "seasonal_naive_7": "Repeat last week",
    "seasonal_mean_7": "Same weekday average",
}

# The pattern token stays internal; this is what a reader sees.
PATTERN_LABELS = {
    "fulfilment_constraint": "Supply could not keep up",
    "demand_softness": "Demand weakened",
    "portfolio_mix_shift": "The sales mix changed",
    "unclassified": "Not yet clear",
}

PATTERN_SUMMARIES = {
    "fulfilment_constraint": (
        "Customers kept ordering, but a smaller share of those orders was "
        "delivered."
    ),
    "demand_softness": (
        "Fewer units were ordered, and deliveries fell in step rather than "
        "ahead of them."
    ),
    "portfolio_mix_shift": (
        "Demand moved between segments that already delivered at different "
        "rates, so the overall figure moved without any segment changing."
    ),
    "unclassified": (
        "Nothing moved decisively enough to separate a demand explanation from "
        "a supply one."
    ),
}

CRITERION_LABELS = {
    "movement_stands_out": "Bigger than this segment's usual swing",
    "movement_persisted": "Moved the same way for more than one period",
    "segment_carries_the_movement": "This segment accounts for much of the change",
    "other_metrics_agree": "Other measures point the same way",
    "alternative_not_supported": "The competing explanation does not fit",
    "movement_is_directional": "A real shift, not segments cancelling out",
}

CONFIDENCE_LABELS = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}

DIRECTION_LABELS = {"up": "Up", "down": "Down", "flat": "Steady"}

# Plain definitions for terms a reader may not have met. Shown in tooltips and
# glossaries rather than assumed.
GLOSSARY = {
    "Fill rate": "The share of ordered units that were actually delivered. 0.95 means 95 of every 100 units ordered arrived.",
    "Average order value": "Net sales divided by the number of orders.",
    "Primary (sell-in)": "Sales into the trade: what distributors and retailers bought from us.",
    "Secondary (sell-through)": "Sales out of the trade: what end customers bought from them.",
    "Stockout rate": "The share of product-days where a SKU had no stock in a warehouse.",
    "Error rate (WAPE)": "Total forecast error as a share of total actual volume. Lower is better; 0.08 means the forecast was off by 8% of the volume it was predicting.",
    "Better than baseline": "How much the error rate improves on simply repeating the last observed value.",
    "From fulfilment": "How much of the change came from segments delivering a different share of their orders.",
    "From demand mix": "How much of the change came from demand moving between segments that already delivered at different rates.",
    "Consistency": "Whether a component moved in one direction across the business, or whether segments cancelled each other out. Near zero means reshuffling, not a trend.",
    "Estimated impact": "Revenue associated with demand that went unserved. An estimate of what was at stake, not a measured loss.",
    "Confidence": "How many of six named checks the evidence passed. It is not a probability.",
    "Gap": "Actual minus what it was compared against. Negative means below.",
    "Share of the change": "This segment's share of the net movement. It can exceed 100% when segments move in opposite directions.",
}


def label_for(name: str) -> str:
    """The readable label for a column, falling back to a tidied version."""
    key = str(name)
    if key in COLUMN_LABELS:
        return COLUMN_LABELS[key]
    return key.replace("_", " ").strip().capitalize()


def humanise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename a frame's columns for display.

    Returns a copy. The renamed frame is for reading; computing against it would
    depend on labels that exist to be changed.
    """
    return frame.rename(columns={column: label_for(column) for column in frame.columns})


# --------------------------------------------------------------------------
# Identifier codes to names
# --------------------------------------------------------------------------


def build_name_lookup(tables: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Map identifier codes to the names the dimensions already hold.

    A tool built for key account managers should be able to name them. The
    customer master carries those names; nothing but habit keeps `K001` on the
    screen.
    """
    lookup: dict[str, str] = {}
    sources = (
        ("dim_region", "region_code", "city"),
        ("dim_kam", "kam_id", "kam_name"),
        ("dim_sku", "sku_id", "sku_name"),
        ("dim_customer", "customer_id", "customer_name"),
    )
    for table, key, name in sources:
        frame = tables.get(table)
        if frame is None or key not in frame.columns or name not in frame.columns:
            continue
        lookup.update(dict(zip(frame[key].astype(str), frame[name].astype(str))))
    return lookup


def humanise_identifiers(
    frame: pd.DataFrame,
    lookup: dict[str, str],
    columns: tuple[str, ...] = ("region_code", "kam_id", "sku_id", "customer_id"),
) -> pd.DataFrame:
    """Replace identifier codes with names in the given columns.

    Returns a copy. Codes that have no name are left as they are rather than
    blanked, so a missing lookup is visible instead of silently erasing a row's
    identity.
    """
    out = frame.copy()
    for column in columns:
        if column in out.columns:
            out[column] = out[column].astype(str).map(lambda value: lookup.get(value, value))
    return out


def humanise_segment(segment: str, lookup: dict[str, str]) -> str:
    """Render a composite segment label in names rather than codes."""
    parts = [part.strip() for part in str(segment).split("|")]
    return " · ".join(lookup.get(part, part) for part in parts)


# --------------------------------------------------------------------------
# Signals and forecasting
# --------------------------------------------------------------------------


def humanise_statement(text: str, segment: str, lookup: dict[str, str]) -> str:
    """Swap a raw segment label for its readable form inside generated prose.

    The signal layer builds its statements around the segment key, because that
    key is stable and every test depends on it. Substituting here keeps the
    generated text intact while showing a reader a place rather than a code.
    """
    if not segment:
        return str(text)
    return str(text).replace(segment, humanise_segment(segment, lookup))


def describe_pattern(pattern: str) -> str:
    """The reader-facing name for a driver pattern."""
    return PATTERN_LABELS.get(pattern, label_for(pattern))


def explain_pattern(pattern: str) -> str:
    """A sentence saying what the pattern means, without jargon."""
    return PATTERN_SUMMARIES.get(pattern, "")


def describe_criterion(name: str) -> str:
    """The reader-facing name for a confidence check."""
    return CRITERION_LABELS.get(name, label_for(name))


def describe_model(name: str) -> str:
    """The reader-facing name for a forecasting method."""
    return MODEL_LABELS.get(name, label_for(name))


def describe_direction(direction: str) -> str:
    return DIRECTION_LABELS.get(direction, label_for(direction))


def glossary_for(labels: list[str]) -> dict[str, str]:
    """The glossary entries relevant to a given set of labels."""
    return {label: GLOSSARY[label] for label in labels if label in GLOSSARY}


def for_display(
    frame: pd.DataFrame,
    tables: dict[str, pd.DataFrame] | None = None,
    lookup: dict[str, str] | None = None,
    drop: tuple[str, ...] = (),
) -> pd.DataFrame:
    """Prepare a frame for a reader: names instead of codes, labels instead of keys.

    The one call a page should need. Returns a copy for display only.
    """
    out = frame.drop(columns=[column for column in drop if column in frame.columns])
    names = lookup if lookup is not None else (build_name_lookup(tables) if tables else {})

    if "model" in out.columns:
        out = out.copy()
        out["model"] = out["model"].astype(str).map(describe_model)
    if "likely_driver" in out.columns:
        out = out.copy()
        out["likely_driver"] = out["likely_driver"].astype(str).map(describe_pattern)
    if "criterion" in out.columns:
        out = out.copy()
        out["criterion"] = out["criterion"].astype(str).map(describe_criterion)
    if "direction" in out.columns:
        out = out.copy()
        out["direction"] = out["direction"].astype(str).map(describe_direction)
    if "observation" in out.columns:
        # This column holds measure names as values, so the values need the same
        # translation the headers get.
        out = out.copy()
        out["observation"] = out["observation"].astype(str).map(label_for)
    if names and "segment" in out.columns:
        out = out.copy()
        out["segment"] = out["segment"].astype(str).map(lambda s: humanise_segment(s, names))
    if names:
        out = humanise_identifiers(out, names)

    return humanise_columns(out)
