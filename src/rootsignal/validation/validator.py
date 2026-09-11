from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .contracts import TABLE_CONTRACTS, TableContract, coerce_dates


@dataclass(frozen=True)
class ValidationIssue:
    table: str
    check: str
    severity: str
    rows: int
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)

    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "ERROR")

    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "WARNING")


class DatasetValidator:
    """Validate raw RootSignal tables without silently repairing defects."""

    def __init__(self, contracts: dict[str, TableContract] | None = None) -> None:
        self.contracts = contracts or TABLE_CONTRACTS

    def validate(self, tables: dict[str, pd.DataFrame]) -> ValidationReport:
        issues: list[ValidationIssue] = []
        for table_name, contract in self.contracts.items():
            frame = tables.get(table_name)
            if frame is None:
                issues.append(ValidationIssue(table_name, "table_present", "ERROR", 0, "Required table is missing."))
                continue
            issues.extend(self._validate_table(table_name, frame, contract))

        issues.extend(self._validate_referential_integrity(tables))
        issues.extend(self._validate_business_rules(tables))
        return ValidationReport(tuple(issues))

    def _validate_table(self, name: str, frame: pd.DataFrame, contract: TableContract) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        missing = [column for column in contract.required_columns if column not in frame.columns]
        if missing:
            issues.append(
                ValidationIssue(name, "required_columns", "ERROR", len(missing), f"Missing columns: {', '.join(missing)}")
            )
            return issues

        null_mask = frame[list(contract.required_columns)].isna()
        null_rows = int(null_mask.any(axis=1).sum())
        if null_rows:
            issues.append(
                ValidationIssue(name, "required_values", "ERROR", null_rows, "Required fields contain null values.")
            )

        if contract.key_columns:
            duplicate_mask = frame.duplicated(list(contract.key_columns), keep=False)
            duplicate_rows = int(duplicate_mask.sum())
            if duplicate_rows:
                issues.append(
                    ValidationIssue(name, "duplicate_key", "ERROR", duplicate_rows, f"Duplicate key values found for {contract.key_columns}.")
                )

        numeric_columns = set(contract.non_negative_columns) | set(contract.bounded_columns)
        for column in numeric_columns:
            if column not in frame.columns:
                continue
            numeric = pd.to_numeric(frame[column], errors="coerce")
            invalid_numeric = int(numeric.isna().sum())
            if invalid_numeric:
                issues.append(
                    ValidationIssue(name, f"numeric_{column}", "ERROR", invalid_numeric, f"Column '{column}' contains non-numeric values.")
                )
            if column in contract.non_negative_columns:
                invalid = int((numeric < 0).sum())
                if invalid:
                    issues.append(
                        ValidationIssue(name, f"non_negative_{column}", "ERROR", invalid, f"Column '{column}' contains negative values.")
                    )
            if column in contract.bounded_columns:
                low, high = contract.bounded_columns[column]
                invalid = int(((numeric < low) | (numeric > high)).sum())
                if invalid:
                    issues.append(
                        ValidationIssue(name, f"bounds_{column}", "ERROR", invalid, f"Column '{column}' falls outside [{low}, {high}].")
                    )

        return issues

    def _validate_referential_integrity(self, tables: dict[str, pd.DataFrame]) -> list[ValidationIssue]:
        relations = (
            ("dim_customer", "region_code", "dim_region", "region_code"),
            ("dim_customer", "kam_id", "dim_kam", "kam_id"),
            ("fact_sales", "customer_id", "dim_customer", "customer_id"),
            ("fact_sales", "region_code", "dim_region", "region_code"),
            ("fact_sales", "kam_id", "dim_kam", "kam_id"),
            ("fact_sales", "sku_id", "dim_sku", "sku_id"),
            ("fact_orders", "customer_id", "dim_customer", "customer_id"),
            ("fact_orders", "region_code", "dim_region", "region_code"),
            ("fact_orders", "sku_id", "dim_sku", "sku_id"),
            ("fact_inventory", "sku_id", "dim_sku", "sku_id"),
            ("fact_inventory", "region_code", "dim_region", "region_code"),
            ("fact_targets", "region_code", "dim_region", "region_code"),
            ("fact_kam_targets", "kam_id", "dim_kam", "kam_id"),
        )
        issues: list[ValidationIssue] = []
        for child, child_col, parent, parent_col in relations:
            child_frame = tables.get(child)
            parent_frame = tables.get(parent)
            if child_frame is None or parent_frame is None or child_col not in child_frame.columns or parent_col not in parent_frame.columns:
                continue
            valid = set(parent_frame[parent_col].dropna().astype(str))
            invalid = ~child_frame[child_col].astype(str).isin(valid)
            count = int(invalid.sum())
            if count:
                issues.append(
                    ValidationIssue(child, f"fk_{child_col}", "ERROR", count, f"Values in '{child_col}' do not resolve to '{parent}.{parent_col}'.")
                )

        if "dim_date" in tables and "date" in tables["dim_date"].columns:
            valid_dates = set(pd.to_datetime(tables["dim_date"]["date"], errors="coerce").dt.date.dropna())
            for table in ("fact_sales", "fact_orders", "fact_inventory", "fact_targets", "fact_kam_targets"):
                frame = tables.get(table)
                if frame is None or "date" not in frame.columns:
                    continue
                dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
                count = int((dates.notna() & ~dates.isin(valid_dates)).sum())
                if count:
                    issues.append(ValidationIssue(table, "fk_date", "ERROR", count, "Fact dates do not resolve to dim_date."))
        return issues

    def _validate_business_rules(self, tables: dict[str, pd.DataFrame]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        orders = tables.get("fact_orders")
        if orders is not None:
            impossible = orders["fulfilled_units"] > orders["ordered_units"]
            count = int(impossible.sum())
            if count:
                issues.append(
                    ValidationIssue("fact_orders", "fulfilled_not_greater_than_ordered", "ERROR", count, "Fulfilled units cannot exceed ordered units.")
                )
            reconciliation = orders["fulfilled_units"] + orders["cancelled_units"] != orders["ordered_units"]
            count = int(reconciliation.sum())
            if count:
                issues.append(
                    ValidationIssue("fact_orders", "order_quantity_reconciliation", "ERROR", count, "Fulfilled + cancelled units must equal ordered units.")
                )

        sales = tables.get("fact_sales")
        if sales is not None:
            expected = (sales["units"] * sales["unit_price"] * (1 - sales["discount_pct"])).round(2)
            mismatch = (expected - sales["net_sales"]).abs() > 0.02
            count = int(mismatch.sum())
            if count:
                issues.append(
                    ValidationIssue("fact_sales", "sales_value_reconciliation", "ERROR", count, "Net sales do not reconcile to units, price, and discount.")
                )

        inventory = tables.get("fact_inventory")
        if inventory is not None:
            stock_flag = inventory["available_stock"] <= 2
            inconsistent = (inventory["stockout_flag"].astype(int) == 1) & ~stock_flag
            count = int(inconsistent.sum())
            if count:
                issues.append(
                    ValidationIssue("fact_inventory", "stockout_flag_consistency", "WARNING", count, "Some stockout flags are set without low available stock; review upstream business rules.")
                )
        return issues


def load_csv_directory(directory: str | Path) -> dict[str, pd.DataFrame]:
    path = Path(directory)
    tables: dict[str, pd.DataFrame] = {}
    for csv_path in sorted(path.glob("*.csv")):
        frame = pd.read_csv(csv_path)
        if "date" in frame.columns:
            frame = coerce_dates(frame)
        tables[csv_path.stem] = frame
    return tables
