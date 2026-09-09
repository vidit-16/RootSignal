from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class TableContract:
    required_columns: tuple[str, ...]
    key_columns: tuple[str, ...] = ()
    non_negative_columns: tuple[str, ...] = ()
    bounded_columns: dict[str, tuple[float, float]] = field(default_factory=dict)


TABLE_CONTRACTS: dict[str, TableContract] = {
    "dim_date": TableContract(("date", "week", "month", "quarter", "year"), ("date",)),
    "dim_kam": TableContract(("kam_id", "kam_name"), ("kam_id",)),
    "dim_region": TableContract(("region_code", "city", "zone"), ("region_code",)),
    "dim_sku": TableContract(
        ("sku_id", "sku_name", "category", "sub_category", "pack_size_kg", "unit_cost", "list_price", "active_flag"),
        ("sku_id",),
        ("pack_size_kg", "unit_cost", "list_price"),
    ),
    "dim_customer": TableContract(
        ("customer_id", "customer_name", "customer_type", "channel", "region_code", "city", "zone", "kam_id"),
        ("customer_id",),
    ),
    "fact_sales": TableContract(
        ("order_id", "date", "customer_id", "region_code", "channel", "kam_id", "sku_id", "category", "sales_type", "units", "unit_price", "discount_pct", "net_sales"),
        (),
        ("units", "unit_price", "net_sales"),
        {"discount_pct": (0.0, 1.0)},
    ),
    "fact_orders": TableContract(
        ("order_id", "date", "customer_id", "region_code", "channel", "sku_id", "ordered_units", "fulfilled_units", "cancelled_units", "order_status", "sales_type"),
        (),
        ("ordered_units", "fulfilled_units", "cancelled_units"),
    ),
    "fact_inventory": TableContract(
        ("date", "sku_id", "warehouse", "region_code", "opening_stock", "received_units", "available_stock", "ordered_units", "fulfilled_units", "stockout_flag"),
        ("date", "sku_id", "warehouse"),
        ("opening_stock", "received_units", "available_stock", "ordered_units", "fulfilled_units"),
        {},
    ),
    "fact_targets": TableContract(
        ("date", "region_code", "category", "channel", "sales_target", "order_target", "fill_rate_target"),
        ("date", "region_code", "category", "channel"),
        ("sales_target", "order_target"),
        {"fill_rate_target": (0.0, 1.0)},
    ),
}


def coerce_dates(frame: pd.DataFrame, columns: tuple[str, ...] = ("date",)) -> pd.DataFrame:
    cleaned = frame.copy()
    for column in columns:
        if column in cleaned.columns:
            cleaned[column] = pd.to_datetime(cleaned[column], errors="coerce").dt.date
    return cleaned
