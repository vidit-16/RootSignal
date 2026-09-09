from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


DEFAULT_COMMERCIAL_GRAIN = ["date", "region_code", "category", "channel", "sales_type"]


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def _require_unique_dimension(dimension: pd.DataFrame, key: str, name: str) -> None:
    _require_columns(dimension, [key], name)
    duplicate_count = int(dimension[key].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"{name} contains {duplicate_count} duplicate '{key}' values.")


def _left_enrich(
    fact: pd.DataFrame,
    dimension: pd.DataFrame,
    key: str,
    columns: Sequence[str],
    name: str,
) -> pd.DataFrame:
    _require_unique_dimension(dimension, key, name)
    _require_columns(fact, [key], "fact")
    _require_columns(dimension, [key, *columns], name)

    lookup = dimension[[key, *columns]].copy()
    result = fact.merge(lookup, on=key, how="left", validate="many_to_one")
    if len(result) != len(fact):
        raise ValueError(f"{name} enrichment changed fact row count unexpectedly.")
    return result


def enrich_sales(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Enrich cleaned sales rows without changing their order-line grain."""
    sales = tables["fact_sales"].copy()
    original_rows = len(sales)

    sales = _left_enrich(
        sales,
        tables["dim_sku"],
        "sku_id",
        ["sku_name", "sub_category", "pack_size_kg", "unit_cost", "list_price"],
        "dim_sku",
    )
    sales = _left_enrich(
        sales,
        tables["dim_customer"],
        "customer_id",
        ["customer_name", "customer_type"],
        "dim_customer",
    )
    sales = _left_enrich(sales, tables["dim_kam"], "kam_id", ["kam_name"], "dim_kam")
    sales = _left_enrich(sales, tables["dim_region"], "region_code", ["city", "zone"], "dim_region")

    if len(sales) != original_rows:
        raise ValueError("Sales enrichment changed row count.")
    return sales


def enrich_orders(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Enrich cleaned order lines with product and customer attributes."""
    orders = tables["fact_orders"].copy()
    original_rows = len(orders)

    orders = _left_enrich(
        orders,
        tables["dim_sku"],
        "sku_id",
        ["sku_name", "category", "sub_category", "pack_size_kg"],
        "dim_sku",
    )
    orders = _left_enrich(
        orders,
        tables["dim_customer"],
        "customer_id",
        ["customer_name", "customer_type"],
        "dim_customer",
    )
    orders = _left_enrich(orders, tables["dim_region"], "region_code", ["city", "zone"], "dim_region")

    if len(orders) != original_rows:
        raise ValueError("Order enrichment changed row count.")
    return orders


def build_commercial_mart(
    tables: dict[str, pd.DataFrame],
    grain: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Build a fact-safe commercial mart from separately aggregated facts.

    Sales and order facts are first reduced to the requested common grain.
    They are then merged one-to-one, preventing row multiplication when an
    order contains multiple SKU lines.
    """
    grain_columns = list(grain or DEFAULT_COMMERCIAL_GRAIN)
    required_grain = set(grain_columns)
    if "date" not in required_grain:
        raise ValueError("Commercial mart grain must include 'date'.")
    if "sales_type" not in required_grain:
        raise ValueError("Commercial mart grain must include 'sales_type'.")

    sales = enrich_sales(tables)
    orders = enrich_orders(tables)

    _require_columns(
        sales,
        [*grain_columns, "order_id", "units", "unit_price", "net_sales"],
        "fact_sales",
    )
    _require_columns(
        orders,
        [*grain_columns, "order_id", "ordered_units", "fulfilled_units", "cancelled_units"],
        "fact_orders",
    )

    sales_with_gross = sales.assign(gross_sales=sales["units"] * sales["unit_price"])
    sales_agg = (
        sales_with_gross.groupby(grain_columns, dropna=False, as_index=False)
        .agg(
            gross_sales=("gross_sales", "sum"),
            net_sales=("net_sales", "sum"),
            sales_units=("units", "sum"),
            sales_order_count=("order_id", "nunique"),
        )
    )

    orders_agg = (
        orders.groupby(grain_columns, dropna=False, as_index=False)
        .agg(
            order_count=("order_id", "nunique"),
            ordered_units=("ordered_units", "sum"),
            fulfilled_units=("fulfilled_units", "sum"),
            cancelled_units=("cancelled_units", "sum"),
        )
    )

    mart = sales_agg.merge(
        orders_agg,
        on=grain_columns,
        how="outer",
        validate="one_to_one",
    )

    numeric_columns = [
        "gross_sales",
        "net_sales",
        "sales_units",
        "sales_order_count",
        "order_count",
        "ordered_units",
        "fulfilled_units",
        "cancelled_units",
    ]
    for column in numeric_columns:
        if column in mart.columns:
            mart[column] = mart[column].fillna(0)

    mart["fill_rate"] = mart["fulfilled_units"].div(
        mart["ordered_units"].where(mart["ordered_units"] != 0)
    ).round(4)
    mart["cancellation_rate"] = mart["cancelled_units"].div(
        mart["ordered_units"].where(mart["ordered_units"] != 0)
    ).round(4)
    mart["aov"] = mart["net_sales"].div(
        mart["sales_order_count"].where(mart["sales_order_count"] != 0)
    ).round(2)
    mart["discount_value"] = (mart["gross_sales"] - mart["net_sales"]).round(2)
    mart["gross_sales"] = mart["gross_sales"].round(2)
    mart["net_sales"] = mart["net_sales"].round(2)

    return mart.sort_values(grain_columns).reset_index(drop=True)
