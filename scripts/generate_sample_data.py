"""Generate the controlled RootSignal sample business dataset.

The generator is deterministic (seed 42) and creates a 60-day FMCG/D2C-style
business dataset with sales, orders, inventory, targets, customers, KAMs, SKUs,
and calendar dimensions.

The raw fact tables intentionally contain a small number of quality defects.
Those defects are part of the test scenario and should be detected by the
validation pipeline rather than silently repaired here.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START_DATE = date(2026, 1, 1)
N_DAYS = 60

REGIONS = [
    ("BLR", "Bengaluru", "South"),
    ("MUM", "Mumbai", "West"),
    ("HYD", "Hyderabad", "South"),
    ("DEL", "Delhi NCR", "North"),
]
WAREHOUSES = {code: f"WH-{code}" for code, _, _ in REGIONS}
KAMS = [
    ("K001", "Aarav Mehta"),
    ("K002", "Ishita Rao"),
    ("K003", "Kabir Shah"),
    ("K004", "Naina Kapoor"),
]
CATEGORIES = {
    "Fruits": ["Mango", "Pomegranate", "Apple", "Banana"],
    "Vegetables": ["Tomato", "Potato", "Broccoli", "Spinach"],
    "Herbs": ["Coriander", "Mint", "Basil"],
    "Premium": ["Avocado", "Kiwi", "Blueberry"],
}
CUSTOMER_TYPES = ["Retail Chain", "Distributor", "D2C", "Foodservice"]
CHANNELS = ["Modern Trade", "General Trade", "D2C", "Foodservice"]


def build_dimensions(rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    dates = [START_DATE + timedelta(days=i) for i in range(N_DAYS)]

    date_df = pd.DataFrame({"date": dates})
    dt = pd.to_datetime(date_df["date"])
    date_df["week"] = dt.dt.isocalendar().week.astype(int)
    date_df["month"] = dt.dt.month
    date_df["quarter"] = "Q" + dt.dt.quarter.astype(str)
    date_df["year"] = dt.dt.year

    sku_rows: list[list[object]] = []
    sku_no = 1
    for category, items in CATEGORIES.items():
        for item in items:
            for tier in ("Standard", "Premium"):
                sku_rows.append([f"SKU{sku_no:03d}", item, category, tier])
                sku_no += 1

    sku_df = pd.DataFrame(
        sku_rows, columns=["sku_id", "sku_name", "category", "sub_category"]
    )
    sku_df["pack_size_kg"] = sku_df["sub_category"].map(
        {"Standard": 1.0, "Premium": 0.5}
    )
    base_price = {"Fruits": 170.0, "Vegetables": 95.0, "Herbs": 75.0, "Premium": 420.0}
    sku_df["list_price"] = sku_df["category"].map(base_price) * (
        1 + sku_df["sub_category"].eq("Premium") * 0.35
    )
    sku_df["unit_cost"] = sku_df["list_price"] * rng.uniform(0.62, 0.76, len(sku_df))
    sku_df["active_flag"] = True

    kam_df = pd.DataFrame(KAMS, columns=["kam_id", "kam_name"])

    customer_rows: list[list[object]] = []
    for i in range(1, 81):
        region_code, city, zone = random.choice(REGIONS)
        customer_type = random.choice(CUSTOMER_TYPES)
        channel = CHANNELS[CUSTOMER_TYPES.index(customer_type)]
        kam_id, _ = random.choice(KAMS)
        customer_rows.append(
            [
                f"C{i:03d}",
                f"Account {i:03d}",
                customer_type,
                channel,
                region_code,
                city,
                zone,
                kam_id,
            ]
        )

    customer_df = pd.DataFrame(
        customer_rows,
        columns=[
            "customer_id",
            "customer_name",
            "customer_type",
            "channel",
            "region_code",
            "city",
            "zone",
            "kam_id",
        ],
    )

    region_df = pd.DataFrame(REGIONS, columns=["region_code", "city", "zone"])
    return {
        "dim_date": date_df,
        "dim_sku": sku_df,
        "dim_customer": customer_df,
        "dim_kam": kam_df,
        "dim_region": region_df,
    }


def build_transactions(
    rng: np.random.Generator,
    dimensions: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = dimensions["dim_date"]["date"].tolist()
    customer_df = dimensions["dim_customer"]
    sku_df = dimensions["dim_sku"]

    sales_rows: list[list[object]] = []
    order_rows: list[list[object]] = []
    order_counter = 100000

    for current_date in dates:
        weekday = current_date.weekday()
        seasonal = 1 + (0.14 if weekday in (4, 5) else (-0.05 if weekday == 0 else 0))

        for _ in range(80):
            order_counter += 1
            customer = customer_df.iloc[random.randrange(len(customer_df))]
            sku = sku_df.iloc[random.randrange(len(sku_df))]

            shock = (
                current_date >= date(2026, 2, 18)
                and customer["region_code"] == "BLR"
                and sku["category"] in ("Fruits", "Vegetables")
            )

            lam = (5.4 if customer["channel"] == "Modern Trade" else 3.3) * seasonal
            ordered_units = max(1, int(rng.poisson(lam)))
            fulfillment_ratio = (
                rng.uniform(0.90, 0.99) if not shock else rng.uniform(0.62, 0.82)
            )

            status = "Cancelled" if rng.random() < 0.025 else "Completed"
            fulfilled_units = (
                max(0, int(round(ordered_units * fulfillment_ratio)))
                if status != "Cancelled"
                else 0
            )
            cancelled_units = (
                ordered_units - fulfilled_units if status != "Cancelled" else ordered_units
            )

            unit_price = float(sku["list_price"] * rng.uniform(0.93, 1.02))
            discount_pct = float(rng.uniform(0, 0.12))
            net_sales = fulfilled_units * unit_price * (1 - discount_pct)
            sales_type = (
                "PRIMARY"
                if customer["customer_type"] in ("Distributor", "Retail Chain")
                and rng.random() < 0.45
                else "SECONDARY"
            )
            order_id = f"O{order_counter}"

            order_rows.append(
                [
                    order_id,
                    current_date,
                    customer["customer_id"],
                    customer["region_code"],
                    customer["channel"],
                    sku["sku_id"],
                    ordered_units,
                    fulfilled_units,
                    cancelled_units,
                    status,
                    sales_type,
                ]
            )

            if fulfilled_units > 0:
                sales_rows.append(
                    [
                        order_id,
                        current_date,
                        customer["customer_id"],
                        customer["region_code"],
                        customer["channel"],
                        customer["kam_id"],
                        sku["sku_id"],
                        sku["category"],
                        sales_type,
                        fulfilled_units,
                        round(unit_price, 2),
                        round(discount_pct, 4),
                        round(net_sales, 2),
                    ]
                )

    sales_df = pd.DataFrame(
        sales_rows,
        columns=[
            "order_id",
            "date",
            "customer_id",
            "region_code",
            "channel",
            "kam_id",
            "sku_id",
            "category",
            "sales_type",
            "units",
            "unit_price",
            "discount_pct",
            "net_sales",
        ],
    )
    orders_df = pd.DataFrame(
        order_rows,
        columns=[
            "order_id",
            "date",
            "customer_id",
            "region_code",
            "channel",
            "sku_id",
            "ordered_units",
            "fulfilled_units",
            "cancelled_units",
            "order_status",
            "sales_type",
        ],
    )
    return sales_df, orders_df


def build_inventory_and_targets(
    rng: np.random.Generator,
    dimensions: dict[str, pd.DataFrame],
    orders_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory_rows: list[list[object]] = []
    dates = dimensions["dim_date"]["date"].tolist()
    sku_df = dimensions["dim_sku"]

    for current_date in dates:
        for region_code, _, _ in REGIONS:
            for _, sku in sku_df.iterrows():
                shock = (
                    current_date >= date(2026, 2, 18)
                    and region_code == "BLR"
                    and sku["category"] in ("Fruits", "Vegetables")
                )

                base_stock = 35 if sku["sub_category"] == "Standard" else 22
                available_stock = max(0, int(rng.normal(base_stock, 7)))
                received_units = int(rng.poisson(15))

                if shock:
                    available_stock = max(0, available_stock - int(rng.integers(8, 18)))
                    received_units = max(0, received_units - int(rng.integers(2, 7)))

                mask = (
                    (orders_df["date"] == current_date)
                    & (orders_df["region_code"] == region_code)
                    & (orders_df["sku_id"] == sku["sku_id"])
                )
                ordered_units = int(orders_df.loc[mask, "ordered_units"].sum())
                fulfilled_units = int(orders_df.loc[mask, "fulfilled_units"].sum())
                stockout_flag = int(
                    available_stock <= 2
                    or (shock and rng.random() < 0.20)
                )

                inventory_rows.append(
                    [
                        current_date,
                        sku["sku_id"],
                        WAREHOUSES[region_code],
                        region_code,
                        max(0, available_stock - received_units),
                        received_units,
                        available_stock,
                        ordered_units,
                        fulfilled_units,
                        stockout_flag,
                    ]
                )

    inventory_df = pd.DataFrame(
        inventory_rows,
        columns=[
            "date",
            "sku_id",
            "warehouse",
            "region_code",
            "opening_stock",
            "received_units",
            "available_stock",
            "ordered_units",
            "fulfilled_units",
            "stockout_flag",
        ],
    )

    target_rows: list[list[object]] = []
    for current_date in dates:
        for region_code, _, _ in REGIONS:
            for category in CATEGORIES:
                sales_target = round(
                    15000
                    * (1 + (0.08 if current_date.weekday() in (4, 5) else 0))
                    * (1 + rng.uniform(-0.04, 0.04)),
                    2,
                )
                order_target = int(80 * (1 + rng.uniform(-0.05, 0.05)))
                target_rows.append(
                    [current_date, region_code, category, "ALL", sales_target, order_target, 0.93]
                )

    targets_df = pd.DataFrame(
        target_rows,
        columns=[
            "date",
            "region_code",
            "category",
            "channel",
            "sales_target",
            "order_target",
            "fill_rate_target",
        ],
    )
    return inventory_df, targets_df


def add_controlled_quality_issues(
    sales_df: pd.DataFrame, orders_df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create deliberate raw-data defects for validation testing."""
    sales_raw = pd.concat([sales_df, sales_df.iloc[[5, 111]]], ignore_index=True)
    sales_raw.loc[17, "discount_pct"] = np.nan
    sales_raw.loc[33, "unit_price"] = np.nan

    orders_raw = orders_df.copy()
    orders_raw.loc[24, "fulfilled_units"] = orders_raw.loc[24, "ordered_units"] + 3
    orders_raw.loc[47, "channel"] = None
    return sales_raw, orders_raw


def write_dataset(output_dir: Path) -> dict[str, object]:
    rng = np.random.default_rng(SEED)
    random.seed(SEED)
    output_dir.mkdir(parents=True, exist_ok=True)

    dimensions = build_dimensions(rng)
    sales_df, orders_df = build_transactions(rng, dimensions)
    inventory_df, targets_df = build_inventory_and_targets(rng, dimensions, orders_df)
    sales_raw, orders_raw = add_controlled_quality_issues(sales_df, orders_df)

    tables = {
        "dim_date": dimensions["dim_date"],
        "dim_sku": dimensions["dim_sku"],
        "dim_customer": dimensions["dim_customer"],
        "dim_kam": dimensions["dim_kam"],
        "dim_region": dimensions["dim_region"],
        "fact_sales": sales_raw,
        "fact_orders": orders_raw,
        "fact_inventory": inventory_df,
        "fact_targets": targets_df,
    }

    row_counts: dict[str, int] = {}
    for name, frame in tables.items():
        frame.to_csv(output_dir / f"{name}.csv", index=False)
        row_counts[name] = len(frame)

    manifest = {
        "seed": SEED,
        "date_range": [str(START_DATE), str(START_DATE + timedelta(days=N_DAYS - 1))],
        "tables": row_counts,
        "controlled_quality_issues": {
            "fact_sales_duplicate_rows": 2,
            "fact_sales_missing_discount_pct": 1,
            "fact_sales_missing_unit_price": 1,
            "fact_orders_fulfilled_gt_ordered": 1,
            "fact_orders_missing_channel": 1,
        },
        "business_scenario": (
            "From 2026-02-18 onward, Bengaluru fruit and vegetable demand remains comparatively "
            "firm while inventory and fulfillment deteriorate. This creates a reproducible scenario "
            "for variance analysis, driver decomposition, impact estimation, and root-cause investigation."
        ),
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the RootSignal sample dataset.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw"),
        help="Directory where generated CSV files and manifest are written.",
    )
    args = parser.parse_args()
    manifest = write_dataset(args.output_dir)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
