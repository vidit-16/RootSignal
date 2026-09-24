"""The PostgreSQL warehouse returns what the SQLite database returns.

The translation tests run everywhere. The warehouse tests need a server and run
when ROOTSIGNAL_WAREHOUSE_URL is set, which CI does with a PostgreSQL service;
locally, `docker compose up -d warehouse` provides one.
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pandas as pd
import pytest

from rootsignal.modeling import build_commercial_mart
from rootsignal.sql import available_queries, build_database, query, run_query_file
from rootsignal.sql.database import ANALYTICS_DIR, MARTS_DIR, SCHEMA_PATH, STAGING_DIR
from rootsignal.sql.dialect import to_postgres
from rootsignal.sql.warehouse import (
    URL_VARIABLE,
    build_warehouse,
    query_warehouse,
    run_warehouse_query_file,
)

MART_KEY = ["date", "region_code", "category", "channel", "sales_type"]
STAGING_VIEWS = ["stg_sales", "stg_orders", "stg_inventory", "stg_targets", "stg_kam_targets"]


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------


def test_week_start_becomes_the_iso_monday() -> None:
    sql = "SELECT DATE(m.date, 'weekday 0', '-6 days') AS week_start FROM t"
    assert to_postgres(sql) == "SELECT CAST(DATE_TRUNC('week', m.date) AS DATE) AS week_start FROM t"


def test_year_month_and_real_are_translated() -> None:
    sql = "SELECT STRFTIME('%Y-%m', date), CAST(x AS REAL) FROM t"
    assert to_postgres(sql) == "SELECT TO_CHAR(date, 'YYYY-MM'), CAST(x AS DOUBLE PRECISION) FROM t"


def test_pragma_is_dropped() -> None:
    assert "PRAGMA" not in to_postgres("PRAGMA foreign_keys = ON;\nCREATE TABLE t (a INTEGER);")


def test_an_untranslatable_construct_is_refused_not_passed_through() -> None:
    with pytest.raises(ValueError, match="JULIANDAY"):
        to_postgres("SELECT JULIANDAY(date) FROM t")


def test_a_construct_mentioned_only_in_a_comment_is_not_refused() -> None:
    assert to_postgres("-- STRFTIME( is SQLite only\nSELECT 1") == "-- STRFTIME( is SQLite only\nSELECT 1"


@pytest.mark.parametrize(
    "path",
    [SCHEMA_PATH, *sorted(STAGING_DIR.glob("*.sql")), *sorted(MARTS_DIR.glob("*.sql")),
     *sorted(ANALYTICS_DIR.glob("*.sql"))],
    ids=lambda path: path.stem,
)
def test_every_sql_file_in_the_repository_translates(path) -> None:
    """A new query using an unhandled construct fails here, not on the server."""
    to_postgres(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The warehouse against the SQLite database
# --------------------------------------------------------------------------

needs_warehouse = pytest.mark.skipif(
    not os.environ.get(URL_VARIABLE),
    reason=f"{URL_VARIABLE} is not set; start one with `docker compose up -d warehouse`",
)


@pytest.fixture(scope="module")
def engines(tmp_path_factory, generated_dataset_dir):
    """Both engines loaded from the same cleaned tables, in a throwaway schema."""
    from rootsignal.cleaning import clean_dataset
    from rootsignal.ingestion import load_dataset

    tables = clean_dataset(load_dataset(generated_dataset_dir)).tables
    schema = f"test_{uuid.uuid4().hex[:10]}"
    warehouse = build_warehouse(tables, schema=schema)
    yield tables, build_database(tables), warehouse
    warehouse.rollback()
    warehouse.execute(f'DROP SCHEMA "{schema}" CASCADE')
    warehouse.commit()
    warehouse.close()


def _assert_same(left: pd.DataFrame, right: pd.DataFrame, tolerance: dict[str, float] | None = None) -> None:
    """Same columns, same row count, same values in the same order."""
    tolerance = tolerance or {}
    assert list(left.columns) == list(right.columns)
    assert len(left) == len(right)
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    for column in left.columns:
        a, b = left[column], right[column]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            np.testing.assert_allclose(
                a.astype(float).to_numpy(),
                b.astype(float).to_numpy(),
                rtol=0,
                atol=tolerance.get(column, 1e-9),
                equal_nan=True,
                err_msg=column,
            )
        else:
            assert a.astype(str).tolist() == b.astype(str).tolist(), column


@needs_warehouse
@pytest.mark.parametrize("view", STAGING_VIEWS)
def test_staging_views_match_sqlite(engines, view) -> None:
    _, sqlite, warehouse = engines
    lite = query(sqlite, f"SELECT * FROM {view}")
    pg = query_warehouse(warehouse, f"SELECT * FROM {view}")
    key = list(lite.columns)
    _assert_same(lite.sort_values(key).reset_index(drop=True), pg.sort_values(key).reset_index(drop=True))


@needs_warehouse
def test_warehouse_mart_matches_the_python_mart(engines) -> None:
    """The warehouse is held to the same definition as the SQLite mart.

    aov may differ by one cent on exact half-cent values. The Python mart rounds
    half to even, SQLite rounds the binary float, and PostgreSQL rounds the
    decimal value half up, so 79.065 is 79.06, 79.06 and 79.07. Every other
    column must agree exactly.
    """
    tables, _, warehouse = engines
    pg = query_warehouse(warehouse, "SELECT * FROM mart_commercial_daily")
    python = build_commercial_mart(tables)
    python["date"] = python["date"].astype(str)
    pg = pg.sort_values(MART_KEY).reset_index(drop=True)
    python = python.sort_values(MART_KEY).reset_index(drop=True)[pg.columns]
    _assert_same(pg, python, tolerance={"aov": 0.01 + 1e-9})


@needs_warehouse
@pytest.mark.parametrize("name", available_queries())
def test_every_analytical_query_matches_sqlite_row_for_row(engines, name) -> None:
    """Same rows in the same order: the queries sort on a full key."""
    _, sqlite, warehouse = engines
    _assert_same(run_query_file(sqlite, name), run_warehouse_query_file(warehouse, name))


@needs_warehouse
def test_a_load_that_breaks_a_foreign_key_leaves_the_warehouse_untouched(engines) -> None:
    tables, _, warehouse = engines
    schema = warehouse.execute("SELECT current_schema()").fetchone()[0]
    before = warehouse.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0]
    warehouse.rollback()

    broken = {name: frame.copy() for name, frame in tables.items()}
    broken["fact_sales"].loc[broken["fact_sales"].index[0], "sku_id"] = "NO-SUCH-SKU"
    with pytest.raises(Exception, match="foreign key"):
        build_warehouse(broken, schema=schema)

    assert warehouse.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0] == before
    warehouse.rollback()
