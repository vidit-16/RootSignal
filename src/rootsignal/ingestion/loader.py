from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pandas as pd

# The business model's tables, in the order the schema declares them.
DATABASE_TABLES = (
    "dim_date",
    "dim_kam",
    "dim_region",
    "dim_sku",
    "dim_customer",
    "fact_sales",
    "fact_orders",
    "fact_inventory",
    "fact_targets",
    "fact_kam_targets",
)


def load_table(path: str | Path, file_type: Literal["csv", "excel"] | None = None) -> pd.DataFrame:
    """Load one tabular source while keeping raw values intact."""
    source = Path(path)
    kind = file_type or source.suffix.lower().lstrip(".")
    if kind == "csv":
        return pd.read_csv(source)
    if kind in {"xlsx", "xls", "excel"}:
        return pd.read_excel(source)
    raise ValueError(f"Unsupported tabular format: {source.suffix}")


def load_dataset(directory: str | Path) -> dict[str, pd.DataFrame]:
    """Load every CSV table from a dataset directory using file stem as table name."""
    path = Path(directory)
    if not path.exists():
        raise FileNotFoundError(f"Dataset directory does not exist: {path}")
    return {p.stem: load_table(p, "csv") for p in sorted(path.glob("*.csv"))}


def load_database_table(connection, table: str) -> pd.DataFrame:
    """Read one table from a live database connection.

    Takes an open DB-API connection rather than a connection string, so the
    caller owns the credentials and the connection lifetime. This module never
    holds either.
    """
    if not str(table).replace("_", "").isalnum():
        raise ValueError(f"Refusing to read a table with an unsafe name: {table!r}")
    return pd.read_sql_query(f"SELECT * FROM {table}", connection)


def load_from_database(connection, tables: Sequence[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load the business tables from a database instead of from files.

    Reads the model's own tables by default. Values arrive exactly as stored,
    with no coercion, because validation and cleaning are the layers entitled to
    change anything.
    """
    names = list(tables) if tables is not None else list(DATABASE_TABLES)
    missing = [name for name in names if name not in _available_tables(connection)]
    if missing:
        raise ValueError(f"Database is missing expected tables: {missing}")
    return {name: load_database_table(connection, name) for name in names}


def _available_tables(connection) -> set[str]:
    """Table names the connection can see, across the common dialects."""
    for statement in (
        "SELECT name FROM sqlite_master WHERE type='table'",
        "SELECT table_name FROM information_schema.tables",
    ):
        try:
            return set(pd.read_sql_query(statement, connection).iloc[:, 0])
        except Exception:  # noqa: BLE001 - dialect probing, try the next form
            continue
    raise ValueError("Could not list tables on this connection; pass `tables` explicitly.")
