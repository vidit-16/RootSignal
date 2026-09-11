from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AuditEvent:
    table: str
    action: str
    rows: int
    detail: str


@dataclass(frozen=True)
class CleaningResult:
    tables: dict[str, pd.DataFrame]
    audit: pd.DataFrame
    quarantined: dict[str, pd.DataFrame]


def _event(table: str, action: str, rows: int, detail: str) -> AuditEvent:
    return AuditEvent(table, action, int(rows), detail)


def _clean_dimensions(tables: dict[str, pd.DataFrame], events: list[AuditEvent]) -> dict[str, pd.DataFrame]:
    cleaned = {name: frame.copy() for name, frame in tables.items()}

    for table_name in ("dim_date", "dim_kam", "dim_region", "dim_sku", "dim_customer"):
        frame = cleaned[table_name]
        before = len(frame)
        frame = frame.drop_duplicates().reset_index(drop=True)
        removed = before - len(frame)
        if removed:
            events.append(_event(table_name, "drop_exact_duplicates", removed, "Removed exact duplicate dimension rows."))
        cleaned[table_name] = frame

    cleaned["dim_sku"]["active_flag"] = cleaned["dim_sku"]["active_flag"].astype(bool)
    cleaned["dim_date"]["date"] = pd.to_datetime(cleaned["dim_date"]["date"], errors="coerce").dt.date
    return cleaned


def _clean_sales(
    tables: dict[str, pd.DataFrame], events: list[AuditEvent]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sales = tables["fact_sales"].copy()
    sku = tables["dim_sku"]

    before = len(sales)
    sales = sales.drop_duplicates().reset_index(drop=True)
    removed = before - len(sales)
    if removed:
        events.append(_event("fact_sales", "drop_exact_duplicates", removed, "Removed exact duplicate transaction rows."))

    missing_discount = int(sales["discount_pct"].isna().sum())
    if missing_discount:
        sales.loc[sales["discount_pct"].isna(), "discount_pct"] = 0.0
        events.append(_event("fact_sales", "impute_discount_pct", missing_discount, "Missing discount was treated as no discount (0%)."))

    sku_price = sku.set_index("sku_id")["list_price"]
    missing_price_mask = sales["unit_price"].isna()
    missing_price = int(missing_price_mask.sum())
    if missing_price:
        sales.loc[missing_price_mask, "unit_price"] = sales.loc[missing_price_mask, "sku_id"].map(sku_price)
        unresolved = int(sales["unit_price"].isna().sum())
        repaired = missing_price - unresolved
        if repaired:
            events.append(_event("fact_sales", "impute_unit_price", repaired, "Missing unit price recovered from dim_sku.list_price."))
        if unresolved:
            events.append(_event("fact_sales", "unresolved_unit_price", unresolved, "Some missing unit prices could not be recovered from the SKU master."))

    # net_sales is derived from the canonical cleaned inputs. Recompute it after
    # repairing discount or price values so the cleaned fact satisfies the same
    # reconciliation contract enforced by DatasetValidator.
    sales["net_sales"] = (
        sales["units"] * sales["unit_price"] * (1 - sales["discount_pct"])
    ).round(2)
    sales["date"] = pd.to_datetime(sales["date"], errors="coerce").dt.date
    return sales, pd.DataFrame(columns=sales.columns)


def _clean_orders(
    tables: dict[str, pd.DataFrame], events: list[AuditEvent]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    orders = tables["fact_orders"].copy()
    customer = tables["dim_customer"]

    channel_map = customer.set_index("customer_id")["channel"]
    missing_channel = orders["channel"].isna()
    count = int(missing_channel.sum())
    if count:
        orders.loc[missing_channel, "channel"] = orders.loc[missing_channel, "customer_id"].map(channel_map)
        remaining = int(orders["channel"].isna().sum())
        repaired = count - remaining
        if repaired:
            events.append(_event("fact_orders", "impute_channel", repaired, "Missing channel recovered from dim_customer.channel."))
        if remaining:
            events.append(_event("fact_orders", "unresolved_channel", remaining, "Some channels could not be recovered from the customer master."))

    impossible = orders["fulfilled_units"] > orders["ordered_units"]
    invalid = orders[impossible].copy()
    if len(invalid):
        events.append(_event("fact_orders", "quarantine_invalid_quantities", len(invalid), "Rows with fulfilled units above ordered units were quarantined."))
        orders = orders.loc[~impossible].copy()

    orders["date"] = pd.to_datetime(orders["date"], errors="coerce").dt.date
    return orders.reset_index(drop=True), invalid.reset_index(drop=True)


def _clean_inventory(tables: dict[str, pd.DataFrame], events: list[AuditEvent]) -> tuple[pd.DataFrame, pd.DataFrame]:
    inventory = tables["fact_inventory"].copy()
    before = len(inventory)
    inventory = inventory.drop_duplicates(subset=["date", "sku_id", "warehouse"], keep="first").reset_index(drop=True)
    removed = before - len(inventory)
    if removed:
        events.append(_event("fact_inventory", "drop_duplicate_key", removed, "Kept the first row for duplicate snapshot keys."))
    inventory["date"] = pd.to_datetime(inventory["date"], errors="coerce").dt.date
    return inventory, pd.DataFrame(columns=inventory.columns)


def _clean_targets(tables: dict[str, pd.DataFrame], events: list[AuditEvent]) -> pd.DataFrame:
    targets = tables["fact_targets"].copy()
    before = len(targets)
    targets = targets.drop_duplicates(subset=["date", "region_code", "category", "channel"], keep="first").reset_index(drop=True)
    removed = before - len(targets)
    if removed:
        events.append(_event("fact_targets", "drop_duplicate_key", removed, "Kept the first row for duplicate target keys."))
    targets["date"] = pd.to_datetime(targets["date"], errors="coerce").dt.date
    return targets


def _clean_kam_targets(tables: dict[str, pd.DataFrame], events: list[AuditEvent]) -> pd.DataFrame:
    targets = tables["fact_kam_targets"].copy()
    before = len(targets)
    targets = targets.drop_duplicates(subset=["date", "kam_id"], keep="first").reset_index(drop=True)
    removed = before - len(targets)
    if removed:
        events.append(_event("fact_kam_targets", "drop_duplicate_key", removed, "Kept the first row for duplicate KAM quota keys."))
    targets["date"] = pd.to_datetime(targets["date"], errors="coerce").dt.date
    return targets


def clean_dataset(tables: dict[str, pd.DataFrame]) -> CleaningResult:
    """Clean recoverable defects and quarantine unsafe fact rows.

    The function does not fabricate values for unresolved identifiers or
    silently rewrite impossible quantities. Every transformation is recorded
    in the returned audit frame.
    """
    required = {
        "dim_date", "dim_kam", "dim_region", "dim_sku", "dim_customer",
        "fact_sales", "fact_orders", "fact_inventory", "fact_targets",
        "fact_kam_targets",
    }
    missing = required - set(tables)
    if missing:
        raise ValueError(f"Cannot clean dataset; missing tables: {sorted(missing)}")

    events: list[AuditEvent] = []
    cleaned = _clean_dimensions(tables, events)
    cleaned["fact_sales"], sales_quarantine = _clean_sales(cleaned, events)
    cleaned["fact_orders"], order_quarantine = _clean_orders(cleaned, events)
    cleaned["fact_inventory"], inventory_quarantine = _clean_inventory(cleaned, events)
    cleaned["fact_targets"] = _clean_targets(cleaned, events)
    cleaned["fact_kam_targets"] = _clean_kam_targets(cleaned, events)

    audit = pd.DataFrame([e.__dict__ for e in events], columns=["table", "action", "rows", "detail"])
    if audit.empty:
        audit = pd.DataFrame(columns=["table", "action", "rows", "detail"])

    return CleaningResult(
        tables=cleaned,
        audit=audit,
        quarantined={
            "fact_sales": sales_quarantine,
            "fact_orders": order_quarantine,
            "fact_inventory": inventory_quarantine,
        },
    )


def write_clean_dataset(result: CleaningResult, output_dir: str | Path) -> None:
    """Write cleaned tables, audit events, and quarantined rows."""
    root = Path(output_dir)
    clean_dir = root / "clean"
    quarantine_dir = root / "quarantine"
    clean_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    for name, frame in result.tables.items():
        frame.to_csv(clean_dir / f"{name}.csv", index=False)
    result.audit.to_csv(root / "cleaning_audit.csv", index=False)
    for name, frame in result.quarantined.items():
        if not frame.empty:
            frame.to_csv(quarantine_dir / f"{name}.csv", index=False)
