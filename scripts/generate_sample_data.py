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
BASE_DAILY_ORDERS = 80

# Orders carry several SKU lines, weighted toward small baskets. A single line
# per order would make order_id unique across both facts, so COUNT(order_id) and
# COUNT(DISTINCT order_id) would agree everywhere and every guard against
# double-counting a multi-SKU order would be unfalsifiable on this data.
BASKET_SIZE_CHOICES = (1, 2, 3)
BASKET_SIZE_WEIGHTS = (0.62, 0.26, 0.12)
MEAN_BASKET_LINES = sum(
    size * weight for size, weight in zip(BASKET_SIZE_CHOICES, BASKET_SIZE_WEIGHTS)
)

# --- Planted scenarios ---------------------------------------------------
# The dataset carries four deliberately different situations. Measuring the
# signal engine against a single planted event only shows that it finds what it
# was pointed at; measuring it against several shows whether it tells them
# apart, which is the claim actually worth making.
# Starts on a Monday, so the constraint occupies whole weeks. Beginning it
# mid-week left the week before it partly affected, which meant the default
# comparison — the latest complete week against the one before — was measuring
# a constrained week against a half-constrained one and reporting a muted
# movement. The event is a step change, so it is only visible where a clean
# period meets an affected one, and that boundary has to fall where the
# periods do.
SUPPLY_CONSTRAINT_START = date(2026, 2, 23)
SUPPLY_CONSTRAINT_REGION = "BLR"
SUPPLY_CONSTRAINT_CATEGORIES = ("Fruits", "Vegetables")

DEMAND_SOFTNESS_START = date(2026, 2, 4)
DEMAND_SOFTNESS_REGION = "HYD"
DEMAND_SOFTNESS_CATEGORIES = ("Premium",)

MIX_SHIFT_START = date(2026, 1, 26)
MIX_SHIFT_REGION = "DEL"
MIX_SHIFT_TOWARD = "Premium"

CONTROL_REGION = "MUM"

SCENARIOS = [
    {
        "name": "blr_supply_constraint",
        "region_code": SUPPLY_CONSTRAINT_REGION,
        "categories": list(SUPPLY_CONSTRAINT_CATEGORIES),
        "starts": str(SUPPLY_CONSTRAINT_START),
        "expected_pattern": "fulfilment_constraint",
        "metric": "fill_rate",
        "current_period": "2026-02-23",
        # The week immediately before, which is now entirely clean. The
        # evaluation and the default behaviour of detect_signals.py therefore
        # examine the same comparison rather than two different ones.
        "comparison_period": "2026-02-16",
        "description": (
            "Fulfilment and stock deteriorate while demand holds or grows. The "
            "segments cannot serve the orders they are receiving."
        ),
    },
    {
        "name": "hyd_demand_softness",
        "region_code": DEMAND_SOFTNESS_REGION,
        "categories": list(DEMAND_SOFTNESS_CATEGORIES),
        "starts": str(DEMAND_SOFTNESS_START),
        "expected_pattern": "demand_softness",
        "metric": "ordered_units",
        "current_period": "2026-02-09",
        "comparison_period": "2026-01-26",
        "description": (
            "Ordered volume falls away while fulfilment and stock stay healthy. "
            "The segment is asked for less, and serves what it is asked for."
        ),
    },
    {
        "name": "del_mix_shift",
        "region_code": MIX_SHIFT_REGION,
        # Left open deliberately. A shift of demand toward one category shows up
        # on the categories *losing* share, which carry the negative mix effect,
        # not on the one gaining it. Naming the gaining category here would ask
        # the engine to surface it in a ranking of negative movements, where it
        # correctly does not appear.
        "categories": [],
        "starts": str(MIX_SHIFT_START),
        "expected_pattern": "portfolio_mix_shift",
        "metric": "fill_rate",
        "current_period": "2026-02-02",
        "comparison_period": "2026-01-19",
        "description": (
            "Demand tilts toward a category this region has always fulfilled "
            "less well. No segment's own fill rate changes; the regional total "
            "falls because the blend moved."
        ),
    },
    {
        "name": "mum_control",
        "region_code": CONTROL_REGION,
        "categories": [],
        "starts": None,
        "expected_pattern": None,
        "metric": "fill_rate",
        "current_period": "2026-01-19",
        "comparison_period": "2026-01-12",
        "description": "Nothing is planted here. Signals raised are false positives.",
    },
]

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


def _pick_skus(
    rng: np.random.Generator,
    sku_df: pd.DataFrame,
    region_code: str,
    current_date: date,
    count: int,
) -> pd.DataFrame:
    """Choose the distinct SKUs on one order.

    In the mix-shift region, demand tilts toward a category that region has
    always fulfilled less well. Nothing about how well any segment fulfils
    changes; only the blend of what is ordered does.
    """
    weights = np.ones(len(sku_df))
    if region_code == MIX_SHIFT_REGION and current_date >= MIX_SHIFT_START:
        weights = np.where(sku_df["category"].to_numpy() == MIX_SHIFT_TOWARD, 3.2, 1.0)
    weights = weights / weights.sum()
    chosen = rng.choice(len(sku_df), size=min(count, len(sku_df)), replace=False, p=weights)
    return sku_df.iloc[chosen]


def _fulfilment_ratio(rng: np.random.Generator, region_code: str, category: str, current_date: date) -> float:
    """How much of a line's demand gets served.

    The supply constraint is an event with a start date. The mix-shift region's
    weaker category is structural and present from day one, so a movement there
    cannot be mistaken for something that happened.
    """
    if (
        region_code == SUPPLY_CONSTRAINT_REGION
        and category in SUPPLY_CONSTRAINT_CATEGORIES
        and current_date >= SUPPLY_CONSTRAINT_START
    ):
        return float(rng.uniform(0.62, 0.82))
    if region_code == MIX_SHIFT_REGION and category == MIX_SHIFT_TOWARD:
        return float(rng.uniform(0.78, 0.86))
    return float(rng.uniform(0.90, 0.99))


def _demand_lambda(
    channel: str,
    basket_seasonal: float,
    region_code: str,
    category: str,
    current_date: date,
) -> float:
    """Expected units on a line, before the scenarios are applied.

    Divided by the mean basket size so that adding lines to an order does not
    silently inflate the business.
    """
    base = (5.4 if channel == "Modern Trade" else 3.3) / MEAN_BASKET_LINES
    lam = base * basket_seasonal
    if (
        region_code == DEMAND_SOFTNESS_REGION
        and category in DEMAND_SOFTNESS_CATEGORIES
        and current_date >= DEMAND_SOFTNESS_START
    ):
        lam *= 0.5
    return lam


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
        # Weekday demand shows up mainly as more orders rather than much larger
        # baskets, so the seasonal effect is split accordingly. Daily order
        # volume also carries genuine noise: a fixed order count per day would
        # make "demand held steady while fulfilment fell" true by construction
        # rather than something the analysis can actually establish.
        order_seasonal = 1 + (0.10 if weekday in (4, 5) else (-0.04 if weekday == 0 else 0))
        basket_seasonal = 1 + (0.04 if weekday in (4, 5) else (-0.01 if weekday == 0 else 0))
        daily_orders = max(
            1,
            int(round(rng.normal(BASE_DAILY_ORDERS * order_seasonal, BASE_DAILY_ORDERS * 0.07))),
        )

        for _ in range(daily_orders):
            order_counter += 1
            order_id = f"O{order_counter}"
            customer = customer_df.iloc[random.randrange(len(customer_df))]
            region_code = customer["region_code"]

            # Order-level attributes are decided once and shared by every line,
            # which is what makes the order the unit that must not be counted
            # more than once downstream.
            status = "Cancelled" if rng.random() < 0.025 else "Completed"
            sales_type = (
                "PRIMARY"
                if customer["customer_type"] in ("Distributor", "Retail Chain")
                and rng.random() < 0.45
                else "SECONDARY"
            )

            basket_size = int(rng.choice(BASKET_SIZE_CHOICES, p=BASKET_SIZE_WEIGHTS))
            for _, sku in _pick_skus(rng, sku_df, region_code, current_date, basket_size).iterrows():
                category = sku["category"]
                lam = _demand_lambda(
                    customer["channel"], basket_seasonal, region_code, category, current_date
                )
                ordered_units = max(1, int(rng.poisson(lam)))
                fulfillment_ratio = _fulfilment_ratio(rng, region_code, category, current_date)

                fulfilled_units = (
                    max(0, round(ordered_units * fulfillment_ratio))
                    if status != "Cancelled"
                    else 0
                )
                cancelled_units = (
                    ordered_units - fulfilled_units if status != "Cancelled" else ordered_units
                )

                unit_price = float(sku["list_price"] * rng.uniform(0.93, 1.02))
                discount_pct = float(rng.uniform(0, 0.12))
                net_sales = fulfilled_units * unit_price * (1 - discount_pct)

                order_rows.append(
                    [
                        order_id,
                        current_date,
                        customer["customer_id"],
                        region_code,
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
                            region_code,
                            customer["channel"],
                            customer["kam_id"],
                            sku["sku_id"],
                            category,
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
    order_df = pd.DataFrame(
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
    return sales_df, order_df


def build_inventory_and_targets(
    rng: np.random.Generator,
    dimensions: dict[str, pd.DataFrame],
    orders_df: pd.DataFrame,
    sales_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory_rows: list[list[object]] = []
    dates = dimensions["dim_date"]["date"].tolist()
    sku_df = dimensions["dim_sku"]

    for current_date in dates:
        for region_code, _, _ in REGIONS:
            for _, sku in sku_df.iterrows():
                shock = (
                    current_date >= SUPPLY_CONSTRAINT_START
                    and region_code == SUPPLY_CONSTRAINT_REGION
                    and sku["category"] in SUPPLY_CONSTRAINT_CATEGORIES
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

    # Targets are a plan, so they are anchored to the demand the business
    # actually sees rather than to a flat constant. A target unrelated to
    # realised volume makes every segment miss by the same implausible margin
    # and leaves plan-versus-actual variance carrying no information.
    #
    # Each segment also carries a persistent plan bias, so some segments run
    # ahead of plan and others fall behind. That spread is what makes variance
    # analysis worth running at all.
    segment_keys = ["region_code", "category", "channel"]
    sku_category = sku_df.set_index("sku_id")["category"]
    orders_by_segment = orders_df.assign(category=orders_df["sku_id"].map(sku_category))

    # Divide by every day in the range, not by the days a segment happened to
    # trade on. At this grain many segments are idle on any given day, and
    # averaging over only their active days would set a plan well above the
    # volume they actually deliver across the period.
    day_count = len(dates)
    sales_baseline = sales_df.groupby(segment_keys)["net_sales"].sum() / day_count
    order_baseline = orders_by_segment.groupby(segment_keys)["order_id"].nunique() / day_count
    plan_bias = centred_plan_bias(rng, sales_baseline.index, 0.07)

    plan_shape = weekday_plan_shape(dates)
    target_rows: list[list[object]] = []
    for current_date in dates:
        weekday_uplift = plan_shape[current_date]
        for region_code, _, _ in REGIONS:
            for category in CATEGORIES:
                for channel in CHANNELS:
                    key = (region_code, category, channel)
                    bias = float(plan_bias.get(key, 1.0))
                    sales_target = round(
                        float(sales_baseline.get(key, 0.0))
                        * bias
                        * weekday_uplift
                        * (1 + rng.uniform(-0.04, 0.04)),
                        2,
                    )
                    order_target = max(
                        1,
                        int(round(float(order_baseline.get(key, 0.0)) * bias * weekday_uplift)),
                    )
                    target_rows.append(
                        [
                            current_date,
                            region_code,
                            category,
                            channel,
                            sales_target,
                            order_target,
                            0.93,
                        ]
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


def centred_plan_bias(rng: np.random.Generator, keys, spread: float) -> dict:
    """Per-owner plan bias that averages to exactly one.

    A plan is written optimistically for some segments and conservatively for
    others, which is what makes variance analysis worth doing. Drawing that
    bias independently also lets the whole plan drift: with four key account
    managers, four draws from a distribution centred on one land above it
    around one time in sixteen, and on this seed they did -- every manager
    behind quota, with nothing in the business to explain it.

    Centring the draws keeps the spread and removes the drift, so attainment
    straddles plan for the reason the docstring claims rather than by luck.
    """
    draws = rng.normal(1.0, spread, size=len(keys))
    draws = draws - draws.mean() + 1.0
    return dict(zip(keys, draws))


def weekday_plan_shape(dates: list[date]) -> dict[date, float]:
    """Plan more for the days that trade harder, without planning more overall.

    Friday and Saturday carry a higher quota than a Tuesday, which is how a
    real plan is written. Applied as a flat multiplier it also raises the total
    plan above the volume it was anchored to -- about 2.3% across a seven-day
    week -- so every segment and every manager would miss quota by that much
    for no reason a business would recognise. With four key account managers
    that was enough to put all four behind plan while the docstring claimed
    some ran ahead.

    Normalising by the mean keeps the shape of the week and removes the
    inflation, so attainment centres on the plan bias rather than on an
    artefact of the uplift.
    """
    raw = {day: (1.08 if day.weekday() in (4, 5) else 1.0) for day in dates}
    mean_uplift = sum(raw.values()) / len(raw)
    return {day: uplift / mean_uplift for day, uplift in raw.items()}


def build_kam_targets(
    rng: np.random.Generator,
    dimensions: dict[str, pd.DataFrame],
    sales_df: pd.DataFrame,
    orders_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build daily revenue and order quotas for each key account manager.

    KAM quotas are a separate planning artifact from the commercial plan in
    fact_targets. A KAM owns a portfolio of customers rather than a region or a
    category, so the quota belongs at its own grain instead of being forced into
    the region/category/channel grain, where most combinations would be empty.

    Like the commercial targets, quotas are anchored to the volume each KAM
    actually delivers and carry a persistent plan bias, so some managers run
    ahead of quota and others behind.
    """
    dates = dimensions["dim_date"]["date"].tolist()
    day_count = len(dates)
    customer_kam = dimensions["dim_customer"].set_index("customer_id")["kam_id"]
    orders_by_kam = orders_df.assign(kam_id=orders_df["customer_id"].map(customer_kam))

    sales_baseline = sales_df.groupby("kam_id")["net_sales"].sum() / day_count
    order_baseline = orders_by_kam.groupby("kam_id")["order_id"].nunique() / day_count
    plan_bias = centred_plan_bias(rng, sales_baseline.index, 0.06)

    rows: list[list[object]] = []
    plan_shape = weekday_plan_shape(dates)
    for current_date in dates:
        weekday_uplift = plan_shape[current_date]
        for kam_id, _ in KAMS:
            bias = float(plan_bias.get(kam_id, 1.0))
            sales_target = round(
                float(sales_baseline.get(kam_id, 0.0))
                * bias
                * weekday_uplift
                * (1 + rng.uniform(-0.05, 0.05)),
                2,
            )
            order_target = max(
                1, int(round(float(order_baseline.get(kam_id, 0.0)) * bias * weekday_uplift))
            )
            rows.append([current_date, kam_id, sales_target, order_target])

    return pd.DataFrame(rows, columns=["date", "kam_id", "sales_target", "order_target"])


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
    inventory_df, targets_df = build_inventory_and_targets(rng, dimensions, orders_df, sales_df)
    kam_targets_df = build_kam_targets(rng, dimensions, sales_df, orders_df)
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
        "fact_kam_targets": kam_targets_df,
    }

    row_counts: dict[str, int] = {}
    for name, frame in tables.items():
        frame.to_csv(output_dir / f"{name}.csv", index=False)
        row_counts[name] = len(frame)

    manifest = {
        "seed": SEED,
        "date_range": [str(START_DATE), str(START_DATE + timedelta(days=N_DAYS - 1))],
        "tables": row_counts,
        "scenarios": SCENARIOS,
        "controlled_quality_issues": {
            "fact_sales_duplicate_rows": 2,
            "fact_sales_missing_discount_pct": 1,
            "fact_sales_missing_unit_price": 1,
            "fact_orders_fulfilled_gt_ordered": 1,
            "fact_orders_missing_channel": 1,
        },
        "business_scenario": (
            "From 2026-02-23 onward, Bengaluru fruit and vegetable demand remains comparatively "
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
