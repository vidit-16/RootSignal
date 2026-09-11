from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from rootsignal.sql import SCHEMA_PATH, build_database, to_sqlite_types
from rootsignal.sql.database import LOAD_ORDER
from rootsignal.validation.contracts import TABLE_CONTRACTS


def _load(tables: dict[str, pd.DataFrame]) -> sqlite3.Connection:
    """Create the declared schema and load the tables, without the views.

    Schema conformance is tested against the tables themselves, so the staging
    and mart views are left out here.
    """
    return build_database(tables, with_views=False)


def _declared_key(conn: sqlite3.Connection, table: str) -> set[str]:
    """Read the primary key SQLite actually enforces for a table."""
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # Columns: (cid, name, type, notnull, default, pk_position); pk_position is 1-based, 0 = not in key.
    return {row[1] for row in info if row[5] > 0}


@pytest.fixture(scope="module")
def empty_schema() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def test_sql_primary_keys_match_python_contracts(empty_schema: sqlite3.Connection) -> None:
    """The schema and the validator must describe the same grain.

    A divergence here means SQL would accept rows DatasetValidator rejects,
    leaving two competing definitions of the same fact grain.
    """
    for table, contract in TABLE_CONTRACTS.items():
        declared = _declared_key(empty_schema, table)
        assert declared == set(contract.key_columns), (
            f"{table}: schema.sql key {sorted(declared)} != contract key {sorted(contract.key_columns)}"
        )


def test_fact_line_tables_are_keyed_by_order_sku_and_sales_type(empty_schema: sqlite3.Connection) -> None:
    """order_id alone is not a valid key: one order may carry several SKUs."""
    for table in ("fact_sales", "fact_orders"):
        assert _declared_key(empty_schema, table) == {"order_id", "sku_id", "sales_type"}


def test_cleaned_dataset_loads_into_declared_schema(cleaned_dataset) -> None:
    """Cleaned output must satisfy every declared constraint, not just the Python ones."""
    tables = cleaned_dataset.tables
    conn = _load(tables)

    for name in LOAD_ORDER:
        loaded = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        assert loaded == len(tables[name]), f"{name}: loaded {loaded} rows, expected {len(tables[name])}"

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_duplicate_transaction_line_is_rejected(cleaned_dataset) -> None:
    """The composite key must actively reject a repeated sales line."""
    tables = cleaned_dataset.tables
    conn = _load(tables)

    duplicate = to_sqlite_types(tables["fact_sales"].head(1))
    with pytest.raises(sqlite3.IntegrityError):
        duplicate.to_sql("fact_sales", conn, if_exists="append", index=False)


def test_multi_sku_order_is_accepted(cleaned_dataset) -> None:
    """The same order_id must still be allowed across different SKUs.

    This is the reason the key is composite: keying on order_id alone would
    reject legitimate multi-SKU orders.
    """
    tables = cleaned_dataset.tables
    conn = _load(tables)

    first_line = tables["fact_sales"].head(1)
    order_id = first_line["order_id"].iloc[0]
    # Orders are baskets, so this order may already carry several lines.
    existing_lines = int((tables["fact_sales"]["order_id"] == order_id).sum())
    used_skus = set(tables["fact_sales"].loc[tables["fact_sales"]["order_id"] == order_id, "sku_id"])
    other_sku = next(s for s in tables["dim_sku"]["sku_id"] if s not in used_skus)

    extra_line = first_line.copy()
    extra_line["sku_id"] = other_sku
    to_sqlite_types(extra_line).to_sql("fact_sales", conn, if_exists="append", index=False)

    lines = conn.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE order_id = ?", (str(order_id),)
    ).fetchone()[0]
    assert lines == existing_lines + 1
