from collections.abc import Sequence

import numpy as np
import pandas as pd

DEFAULT_SALES_GROUPS = ["date"]
DEFAULT_ORDER_GROUPS = ["date"]


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def calculate_sales_kpis(
    sales: pd.DataFrame,
    group_by: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Aggregate sales metrics at a caller-selected grain.

    Orders are counted with nunique(order_id) because fact_sales is an order-line
    table and an order may contain multiple SKU lines.
    """
    groups = list(group_by or DEFAULT_SALES_GROUPS)
    _require_columns(
        sales,
        ["order_id", "units", "unit_price", "discount_pct", "net_sales"],
        "fact_sales",
    )
    _require_columns(sales, groups, "group_by")

    frame = sales.copy()
    frame["gross_sales"] = frame["units"] * frame["unit_price"]
    grouped = frame.groupby(groups, dropna=False, as_index=False).agg(
        gross_sales=("gross_sales", "sum"),
        net_sales=("net_sales", "sum"),
        units=("units", "sum"),
        order_count=("order_id", "nunique"),
    )
    grouped["discount_value"] = grouped["gross_sales"] - grouped["net_sales"]
    grouped["aov"] = np.where(
        grouped["order_count"] > 0,
        grouped["net_sales"] / grouped["order_count"],
        np.nan,
    )
    grouped["net_sales"] = grouped["net_sales"].round(2)
    grouped["gross_sales"] = grouped["gross_sales"].round(2)
    grouped["discount_value"] = grouped["discount_value"].round(2)
    grouped["aov"] = grouped["aov"].round(2)
    return grouped


def calculate_order_kpis(
    orders: pd.DataFrame,
    group_by: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Aggregate order and fulfillment metrics at a caller-selected grain."""
    groups = list(group_by or DEFAULT_ORDER_GROUPS)
    _require_columns(
        orders,
        ["order_id", "ordered_units", "fulfilled_units", "cancelled_units"],
        "fact_orders",
    )
    _require_columns(orders, groups, "group_by")

    grouped = orders.groupby(groups, dropna=False, as_index=False).agg(
        order_count=("order_id", "nunique"),
        ordered_units=("ordered_units", "sum"),
        fulfilled_units=("fulfilled_units", "sum"),
        cancelled_units=("cancelled_units", "sum"),
    )
    grouped["fill_rate"] = calculate_fill_rate(
        grouped["fulfilled_units"], grouped["ordered_units"]
    )
    grouped["cancellation_rate"] = np.where(
        grouped["ordered_units"] > 0,
        grouped["cancelled_units"] / grouped["ordered_units"],
        np.nan,
    )
    return grouped


def calculate_fill_rate(
    fulfilled_units: pd.Series | np.ndarray | float,
    ordered_units: pd.Series | np.ndarray | float,
) -> pd.Series:
    """Return fulfilled / ordered; periods with zero demand are undefined."""
    numerator = pd.Series(fulfilled_units, dtype="float64")
    denominator = pd.Series(ordered_units, dtype="float64")
    return numerator.div(denominator.where(denominator != 0)).round(4)


def calculate_primary_secondary_mix(
    sales: pd.DataFrame,
    group_by: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Return primary/secondary sales side by side with mix percentages."""
    groups = list(group_by or DEFAULT_SALES_GROUPS)
    _require_columns(sales, ["sales_type", "net_sales"], "fact_sales")
    _require_columns(sales, groups, "group_by")

    pivot = (
        sales.pivot_table(
            index=groups,
            columns="sales_type",
            values="net_sales",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    for column in ("PRIMARY", "SECONDARY"):
        if column not in pivot.columns:
            pivot[column] = 0.0
    pivot = pivot.rename(
        columns={"PRIMARY": "primary_sales", "SECONDARY": "secondary_sales"}
    )
    pivot["total_sales"] = pivot["primary_sales"] + pivot["secondary_sales"]
    pivot["primary_mix"] = np.where(
        pivot["total_sales"] > 0,
        pivot["primary_sales"] / pivot["total_sales"],
        np.nan,
    ).round(4)
    pivot["secondary_mix"] = np.where(
        pivot["total_sales"] > 0,
        pivot["secondary_sales"] / pivot["total_sales"],
        np.nan,
    ).round(4)
    return pivot


def add_growth(
    frame: pd.DataFrame,
    value_column: str,
    group_by: Sequence[str] | None = None,
    period_column: str = "date",
) -> pd.DataFrame:
    """Add prior-period and percentage-growth columns within optional groups."""
    _require_columns(frame, [value_column, period_column], "frame")
    groups = list(group_by or [])
    if period_column in groups:
        raise ValueError("period_column must not also appear in group_by")

    result = frame.copy()
    result[period_column] = pd.to_datetime(result[period_column], errors="coerce")
    sort_columns = groups + [period_column]
    result = result.sort_values(sort_columns).reset_index(drop=True)
    if groups:
        result["previous_value"] = result.groupby(groups, dropna=False)[value_column].shift(1)
    else:
        result["previous_value"] = result[value_column].shift(1)
    result["growth_pct"] = np.where(
        result["previous_value"].abs() > 0,
        (result[value_column] - result["previous_value"]) / result["previous_value"],
        np.nan,
    ).round(4)
    return result


def calculate_target_variance(
    actuals: pd.DataFrame,
    targets: pd.DataFrame,
    dimensions: Sequence[str],
    actual_column: str = "net_sales",
    target_column: str = "sales_target",
) -> pd.DataFrame:
    """Compare an already-aggregated actual metric to targets at the same grain.

    The caller must supply compatible grains. No fact-to-fact join is performed.
    """
    dims = list(dimensions)
    _require_columns(actuals, [*dims, actual_column], "actuals")
    _require_columns(targets, [*dims, target_column], "targets")

    actual = actuals.groupby(dims, dropna=False, as_index=False)[actual_column].sum()
    target = targets.groupby(dims, dropna=False, as_index=False)[target_column].sum()
    merged = actual.merge(target, on=dims, how="left", validate="one_to_one")
    merged["variance"] = merged[actual_column] - merged[target_column]
    merged["variance_pct"] = np.where(
        merged[target_column].abs() > 0,
        merged["variance"] / merged[target_column],
        np.nan,
    )
    return merged
