"""Build and query a relational copy of the cleaned business model.

The SQL layer is executable rather than illustrative. These helpers load the
cleaned tables into SQLite under the declared schema, register the staging
views, and run the analytical queries, so every .sql file in the repository is
exercised by the test suite rather than read and trusted.

SQLite is used because it needs no server and makes the SQL runnable anywhere
the tests run. The statements stay close to portable SQL so the same logic moves
to PostgreSQL with little change.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

SQL_ROOT = Path(__file__).resolve().parents[3] / "sql"
SCHEMA_PATH = SQL_ROOT / "schema.sql"
STAGING_DIR = SQL_ROOT / "staging"
MARTS_DIR = SQL_ROOT / "marts"
ANALYTICS_DIR = SQL_ROOT / "analytics"

# Dimensions must be loaded before facts so foreign keys resolve.
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
    "fact_kam_targets",
)


def to_sqlite_types(frame: pd.DataFrame) -> pd.DataFrame:
    """Render a cleaned frame with SQLite-native column types.

    Dates become ISO strings and booleans become 0/1 integers, so the load
    exercises the schema's CHECK constraints rather than relying on sqlite3's
    deprecated default adapters.
    """
    out = frame.copy()
    for column in out.columns:
        if out[column].map(lambda value: isinstance(value, dt.date)).any():
            out[column] = out[column].map(
                lambda value: value.isoformat() if isinstance(value, dt.date) else value
            )
        elif out[column].dtype == bool:
            out[column] = out[column].astype(int)
    return out


def _sql_files(directory: Path) -> list[Path]:
    """Every .sql file in a directory, in a stable order."""
    if not directory.exists():
        return []
    return sorted(directory.glob("*.sql"))


def build_database(
    tables: dict[str, pd.DataFrame],
    destination: str | Path = ":memory:",
    with_views: bool = True,
) -> sqlite3.Connection:
    """Create the schema, load the cleaned tables, and register the views.

    Foreign keys are enforced, so a load that violates the declared model fails
    here rather than producing a quietly inconsistent database.
    """
    conn = sqlite3.connect(str(destination))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # executescript commits, which neutralises the PRAGMA in the schema file.
    conn.execute("PRAGMA foreign_keys = ON")

    missing = [name for name in LOAD_ORDER if name not in tables]
    if missing:
        raise ValueError(f"Cannot build the database; missing tables: {missing}")

    for name in LOAD_ORDER:
        to_sqlite_types(tables[name]).to_sql(name, conn, if_exists="append", index=False)

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError(f"Loaded data violates the declared foreign keys: {violations[:5]}")

    if with_views:
        apply_directory(conn, STAGING_DIR)
        apply_directory(conn, MARTS_DIR)
    return conn


def apply_directory(conn: sqlite3.Connection, directory: Path) -> list[str]:
    """Execute every .sql file in a directory, returning the names applied."""
    applied = []
    for path in _sql_files(directory):
        conn.executescript(path.read_text(encoding="utf-8"))
        applied.append(path.stem)
    conn.commit()
    return applied


def query(conn: sqlite3.Connection, sql: str, params: Sequence | None = None) -> pd.DataFrame:
    """Run a SELECT and return the result as a frame."""
    return pd.read_sql_query(sql, conn, params=list(params) if params else None)


def run_query_file(
    conn: sqlite3.Connection,
    name: str,
    directory: Path | None = None,
    params: Sequence | None = None,
) -> pd.DataFrame:
    """Run a named analytical query from sql/analytics and return its result."""
    folder = directory or ANALYTICS_DIR
    path = folder / f"{name}.sql"
    if not path.exists():
        available = [candidate.stem for candidate in _sql_files(folder)]
        raise ValueError(f"No query named '{name}' in {folder}; available: {available}")
    return query(conn, path.read_text(encoding="utf-8"), params)


def available_queries(directory: Path | None = None) -> list[str]:
    """Names of the analytical queries that can be run."""
    return [path.stem for path in _sql_files(directory or ANALYTICS_DIR)]
