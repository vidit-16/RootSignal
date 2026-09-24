"""Load the cleaned business model into PostgreSQL and query it.

The SQLite database in database.py is what the test suite builds in memory.
This is the same schema, the same views and the same queries on a PostgreSQL
server: the warehouse the pipeline loads into. Nothing is written twice. The
files in sql/ are translated by dialect.to_postgres, and tests assert both
engines return the same rows for every query.

Each load replaces one schema wholesale, so a run is repeatable and a failed
load never leaves a half-updated warehouse behind: the new tables are built in
a transaction and the old schema is only dropped inside the same one.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .database import (
    ANALYTICS_DIR,
    LOAD_ORDER,
    MARTS_DIR,
    SCHEMA_PATH,
    STAGING_DIR,
    _sql_files,
    to_sqlite_types,
)
from .dialect import ROUND_FUNCTION, to_postgres

URL_VARIABLE = "ROOTSIGNAL_WAREHOUSE_URL"
DEFAULT_SCHEMA = "rootsignal"


def warehouse_url(url: str | None = None) -> str:
    """The connection string, from the argument or ROOTSIGNAL_WAREHOUSE_URL."""
    resolved = url or os.environ.get(URL_VARIABLE, "")
    if not resolved:
        raise ValueError(
            f"No warehouse configured. Pass a URL or set {URL_VARIABLE}, for example "
            "postgresql://rootsignal:rootsignal@localhost:5433/rootsignal "
            "(docker compose up -d warehouse)."
        )
    return resolved


def connect(url: str | None = None, schema: str = DEFAULT_SCHEMA):
    """Open a connection whose unqualified names resolve inside `schema`."""
    import psycopg

    conn = psycopg.connect(warehouse_url(url), autocommit=False)
    conn.execute(f'SET search_path TO "{schema}"')
    conn.commit()
    return conn


def _copy_frame(cursor, table: str, frame: pd.DataFrame) -> None:
    """Bulk-load a frame with COPY, which is what makes a million rows cheap.

    CSV is used as the wire format. An empty unquoted field is NULL in COPY's
    CSV mode, which is how pandas writes a missing value.
    """
    buffer = io.StringIO()
    to_sqlite_types(frame).to_csv(buffer, index=False, header=False, na_rep="")
    columns = ", ".join(f'"{column}"' for column in frame.columns)
    with cursor.copy(f'COPY "{table}" ({columns}) FROM STDIN WITH (FORMAT csv)') as copy:
        copy.write(buffer.getvalue())


def build_warehouse(
    tables: dict[str, pd.DataFrame],
    url: str | None = None,
    schema: str = DEFAULT_SCHEMA,
    with_views: bool = True,
):
    """Replace `schema` with the cleaned tables, and register the views.

    Foreign keys are enforced by the server as rows arrive, so a load that
    violates the declared model fails here, and the transaction leaves the
    previous warehouse as it was.
    """
    missing = [name for name in LOAD_ORDER if name not in tables]
    if missing:
        raise ValueError(f"Cannot build the warehouse; missing tables: {missing}")

    import psycopg

    conn = psycopg.connect(warehouse_url(url), autocommit=False)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cursor.execute(f'CREATE SCHEMA "{schema}"')
            cursor.execute(f'SET LOCAL search_path TO "{schema}"')
            cursor.execute(ROUND_FUNCTION)
            cursor.execute(to_postgres(SCHEMA_PATH.read_text(encoding="utf-8")))
            for name in LOAD_ORDER:
                _copy_frame(cursor, name, tables[name])
            if with_views:
                for directory in (STAGING_DIR, MARTS_DIR):
                    for path in _sql_files(directory):
                        cursor.execute(to_postgres(path.read_text(encoding="utf-8")))
            cursor.execute("ANALYZE")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return connect(url, schema)


def _column_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_numeric_dtype(series):
        return "DOUBLE PRECISION"
    if series.map(lambda value: isinstance(value, dt.date)).any():
        return "DATE"
    return "TEXT"


def write_report_table(conn, name: str, frame: pd.DataFrame) -> int:
    """Replace one report table with a frame, typing columns from its dtypes.

    Report tables are outputs of the analysis layer rather than part of the
    declared model, so they carry no keys and are rebuilt on every run.
    """
    columns = ", ".join(f'"{column}" {_column_type(frame[column])}' for column in frame.columns)
    with conn.cursor() as cursor:
        cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
        cursor.execute(f'CREATE TABLE "{name}" ({columns})')
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, header=False, na_rep="")
        names = ", ".join(f'"{column}"' for column in frame.columns)
        with cursor.copy(f'COPY "{name}" ({names}) FROM STDIN WITH (FORMAT csv)') as copy:
            copy.write(buffer.getvalue())
    conn.commit()
    return len(frame)


def _plain(value):
    """Server types as the plain Python values the SQLite path returns."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def query_warehouse(conn, sql: str, params: Sequence | None = None) -> pd.DataFrame:
    """Run a SELECT against the warehouse and return the result as a frame.

    NUMERIC results (AVG of an integer column, for instance) arrive as Decimal
    and are returned as float, and dates as ISO strings, so a frame from either
    engine can be compared with the other directly.
    """
    statement = to_postgres(sql)
    if params:
        statement = statement.replace("?", "%s")
    with conn.cursor() as cursor:
        cursor.execute(statement, list(params) if params else None)
        columns = [column.name for column in cursor.description]
        rows = [[_plain(value) for value in row] for row in cursor.fetchall()]
    conn.rollback()
    frame = pd.DataFrame(rows, columns=columns)
    for column in frame.columns:
        if frame[column].map(lambda value: isinstance(value, dt.date)).any():
            frame[column] = frame[column].map(
                lambda value: value.isoformat() if isinstance(value, dt.date) else value
            )
        elif frame[column].dtype == object and frame[column].map(
            lambda value: value is None or isinstance(value, float)
        ).all():
            frame[column] = frame[column].astype(float)
    return frame


def run_warehouse_query_file(
    conn,
    name: str,
    directory: Path | None = None,
    params: Sequence | None = None,
) -> pd.DataFrame:
    """Run a named analytical query from sql/analytics on the warehouse."""
    folder = directory or ANALYTICS_DIR
    path = folder / f"{name}.sql"
    if not path.exists():
        available = [candidate.stem for candidate in _sql_files(folder)]
        raise ValueError(f"No query named '{name}' in {folder}; available: {available}")
    return query_warehouse(conn, path.read_text(encoding="utf-8"), params)
