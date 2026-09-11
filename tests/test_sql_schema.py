from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from rootsignal.validation.contracts import TABLE_CONTRACTS

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "sql" / "schema.sql"

# Dimensions must land before facts so foreign keys resolve.
LOAD_ORDER = (
    "dim_date",
    "dim_kam",
    "dim_region",
    "dim_sku",
    "dim_customer",
    "fact_sales",
    "fact_orders",
    "fact_inventory",
    "fact_targets",
)


def _sqlite_ready(frame: pd.DataFrame) -> pd.DataFrame:
    """Render a cleaned frame with SQLite-native column types.

    Dates become ISO strings and booleans become 0/1 integers so the load
    exercises the schema's CHECK constraints rather than sqlite3's
    deprecated default adapters.
    """
    out = frame.copy()
    for column in out.columns:
        if out[column].map(lambda v: isinstance(v, dt.date)).any():
            out[column] = out[column].map(lambda v: v.isoformat() if isinstance(v, dt.date) else v)
        elif out[column].dtype == bool:
            out[column] = out[column].astype(int)
    return out


def _build_database(tables: dict[str, pd.DataFrame]) -> sqlite3.Connection:
    """Create the declared schema and load the given tables into it."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # executescript commits, which neutralises the PRAGMA in the file; re-arm it.
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    for name in LOAD_ORDER:
        _sqlite_ready(tables[name]).to_sql(name, conn, if_exists="append", index=False)
    return conn


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
    conn = _build_database(tables)

    for name in LOAD_ORDER:
        loaded = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        assert loaded == len(tables[name]), f"{name}: loaded {loaded} rows, expected {len(tables[name])}"

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_duplicate_transaction_line_is_rejected(cleaned_dataset) -> None:
    """The composite key must actively reject a repeated sales line."""
    tables = cleaned_dataset.tables
    conn = _build_database(tables)

    duplicate = _sqlite_ready(tables["fact_sales"].head(1))
    with pytest.raises(sqlite3.IntegrityError):
        duplicate.to_sql("fact_sales", conn, if_exists="append", index=False)


def test_multi_sku_order_is_accepted(cleaned_dataset) -> None:
    """The same order_id must still be allowed across different SKUs.

    This is the reason the key is composite: keying on order_id alone would
    reject legitimate multi-SKU orders.
    """
    tables = cleaned_dataset.tables
    conn = _build_database(tables)

    first_line = tables["fact_sales"].head(1)
    order_id = first_line["order_id"].iloc[0]
    current_sku = first_line["sku_id"].iloc[0]
    other_sku = next(s for s in tables["dim_sku"]["sku_id"] if s != current_sku)

    extra_line = first_line.copy()
    extra_line["sku_id"] = other_sku
    _sqlite_ready(extra_line).to_sql("fact_sales", conn, if_exists="append", index=False)

    lines = conn.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE order_id = ?", (str(order_id),)
    ).fetchone()[0]
    assert lines == 2
