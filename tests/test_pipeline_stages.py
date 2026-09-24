"""The load and publish stages: what they write, and when they refuse to.

The clean zone is what Redshift loads with COPY, which rejects a Parquet type
that does not match the table, so its types are pinned to schema.sql here.
The report tables must be plain enough for a SQL table to hold.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pandas as pd
import pytest

from rootsignal.adapters.base import AdaptedDataset, DatasetCapabilities
from rootsignal.pipeline import CLEAN, Lake, load, write_clean_zone
from rootsignal.pipeline.publish import flatten
from rootsignal.sql.database import LOAD_ORDER, SCHEMA_PATH
from rootsignal.sql.dialect import schema_columns
from rootsignal.sql.warehouse import URL_VARIABLE, connect, write_report_table
from rootsignal.validation.validator import ValidationIssue, ValidationReport


def test_the_clean_zone_is_typed_exactly_as_the_schema_declares(cleaned_dataset, tmp_path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    expected_types = {"TEXT": pa.string(), "INTEGER": pa.int32(), "REAL": pa.float64(), "DATE": pa.date32()}
    lake = Lake(str(tmp_path))
    write_clean_zone(lake, cleaned_dataset.tables)

    declared = schema_columns(SCHEMA_PATH.read_text(encoding="utf-8"))
    for name in LOAD_ORDER:
        schema = pq.read_schema(tmp_path / CLEAN / name / "part-00000.parquet")
        assert schema.names == [column for column, _ in declared[name]], name
        for column, kind in declared[name]:
            assert schema.field(column).type == expected_types[kind], f"{name}.{column}"


def test_flags_are_written_as_zero_or_one(cleaned_dataset, tmp_path) -> None:
    import pyarrow.parquet as pq

    write_clean_zone(Lake(str(tmp_path)), cleaned_dataset.tables)
    flags = pq.read_table(tmp_path / CLEAN / "dim_sku" / "part-00000.parquet").column("active_flag")
    assert set(flags.to_pylist()) <= {0, 1}


def test_row_counts_survive_the_clean_zone(cleaned_dataset, tmp_path) -> None:
    import pyarrow.parquet as pq

    write_clean_zone(Lake(str(tmp_path)), cleaned_dataset.tables)
    for name in LOAD_ORDER:
        rows = pq.read_metadata(tmp_path / CLEAN / name / "part-00000.parquet").num_rows
        assert rows == len(cleaned_dataset.tables[name]), name


def test_report_tables_are_flattened_to_plain_columns() -> None:
    frame = pd.DataFrame(
        {
            "period_start": pd.PeriodIndex(["2011-10", "2011-11"], freq="M"),
            "stamp": pd.to_datetime(["2011-10-03 09:00", "2011-11-04 10:00"]),
            "value": [1.5, float("nan")],
            "count": [3, 4],
            "complete": [True, False],
            "segments": [("United Kingdom", "France"), None],
        },
        index=pd.Index(["a", "b"], name="kept_out"),
    )

    flat = flatten(frame)

    assert list(flat.columns) == ["period_start", "stamp", "value", "count", "complete", "segments"]
    assert flat["period_start"].tolist() == [dt.date(2011, 10, 1), dt.date(2011, 11, 1)]
    assert flat["stamp"].tolist() == [dt.date(2011, 10, 3), dt.date(2011, 11, 4)]
    assert flat["segments"].tolist() == ["('United Kingdom', 'France')", None]
    assert flat["count"].tolist() == [3, 4] and flat["complete"].tolist() == [True, False]


def test_the_spark_job_is_where_the_pipeline_looks_for_it() -> None:
    from rootsignal.pipeline.stages import JOB_PATH

    assert JOB_PATH.is_file() and JOB_PATH.name == "conform_online_retail.py"


def test_a_failed_spark_run_stops_the_pipeline_with_its_error(monkeypatch, tmp_path) -> None:
    """The job's own last words are what someone debugging it needs."""
    import subprocess

    from rootsignal.pipeline import conform_locally, stages

    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="x" * 6000 + "Py4JJavaError: disk full")
    monkeypatch.setattr(stages.subprocess, "run", lambda *args, **kwargs: failed)

    with pytest.raises(RuntimeError, match="disk full") as raised:
        conform_locally(Lake(str(tmp_path)))
    assert len(str(raised.value)) < 4100, "the whole log is not pasted into the error"


def test_a_run_that_exits_cleanly_is_not_reported_as_failed(monkeypatch, tmp_path) -> None:
    import subprocess

    from rootsignal.pipeline import conform_locally, stages

    monkeypatch.setattr(
        stages.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="WARN only"),
    )
    monkeypatch.setattr(stages, "read_summary", lambda lake: {"source_rows": 3})
    assert conform_locally(Lake(str(tmp_path))) == {"source_rows": 3}


def write_curated_zone(root, cleaned_tables) -> None:
    """A curated zone laid out as the Spark job writes it, without running Spark."""
    import json

    from rootsignal.pipeline import CURATED
    from rootsignal.pipeline.stages import CURATED_TABLES

    for name in CURATED_TABLES:
        folder = root / CURATED / name
        folder.mkdir(parents=True)
        cleaned_tables[name].to_parquet(folder / "part-00000.parquet", index=False)
    returns = pd.DataFrame(
        {"order_id": ["C1"], "sku_id": ["A"], "region_code": ["France"], "customer_id": ["UNKNOWN"],
         "date": [dt.date(2024, 1, 3)], "units": [-2], "returned_units": [2]}
    )
    (root / CURATED / "returns").mkdir(parents=True)
    returns.to_parquet(root / CURATED / "returns" / "part-00000.parquet", index=False)
    (root / CURATED / "_summary").mkdir(parents=True)
    summary = {"source_rows": 1234, "returned_lines": 56, "consolidated_lines": 7, "tables": {}}
    (root / CURATED / "_summary" / "part-00000.txt").write_text(json.dumps(summary), encoding="utf-8")


def test_the_curated_zone_reads_back_as_the_adapter_would_return_it(cleaned_dataset, tmp_path) -> None:
    from rootsignal.pipeline import read_curated
    from rootsignal.pipeline.stages import EMPTY_FACTS

    write_curated_zone(tmp_path, cleaned_dataset.tables)
    dataset, returns = read_curated(Lake(str(tmp_path)))

    assert dataset.source_rows == 1234
    sales = dataset.tables["fact_sales"]
    assert len(sales) == len(cleaned_dataset.tables["fact_sales"])
    assert isinstance(sales["date"].iloc[0], dt.date) and not isinstance(sales["date"].iloc[0], dt.datetime)
    assert isinstance(returns["date"].iloc[0], dt.date)
    for name in EMPTY_FACTS:
        assert name in dataset.tables and dataset.tables[name].empty
    notes = " ".join(dataset.capabilities.notes)
    assert "56 of 1,234 lines are returns" in notes
    assert "7 invoice lines repeat a product" in notes
    assert "Conformed in Spark" in notes


def test_the_report_backtest_is_the_one_behind_the_published_figure() -> None:
    """The README's 38.8% over 49 folds comes from these settings.

    scripts/run_external_dataset.py produces that figure and the report table
    must match it, so both read one constant rather than two copies.
    """
    from pathlib import Path

    from rootsignal.pipeline.publish import BACKTEST

    assert BACKTEST == {"horizon": 7, "initial_train": 56, "step": 14}
    script = (Path(__file__).resolve().parents[1] / "scripts" / "run_external_dataset.py").read_text(encoding="utf-8")
    assert "rolling_origin_evaluate(series, **BACKTEST)" in script


def test_nothing_is_loaded_while_validation_still_fails(monkeypatch, cleaned_dataset) -> None:
    import rootsignal.sql.warehouse as warehouse
    from rootsignal.validation import DatasetValidator

    failing = ValidationReport(
        issues=(ValidationIssue("fact_sales", "keys", "ERROR", 2, "duplicate order lines"),)
    )
    monkeypatch.setattr(DatasetValidator, "validate", lambda self, tables: failing)
    built = []
    monkeypatch.setattr(warehouse, "build_warehouse", lambda *args, **kwargs: built.append(True))
    dataset = AdaptedDataset(
        tables=cleaned_dataset.tables,
        capabilities=DatasetCapabilities(name="test", available_facts=("fact_sales",)),
        source_rows=0,
    )

    with pytest.raises(ValueError, match="fact_sales.keys: duplicate order lines"):
        load(dataset, url="postgresql://unused")
    assert not built


needs_warehouse = pytest.mark.skipif(
    not os.environ.get(URL_VARIABLE),
    reason=f"{URL_VARIABLE} is not set; start one with `docker compose up -d warehouse`",
)


@needs_warehouse
def test_a_report_table_round_trips_through_the_warehouse() -> None:
    schema = f"test_{uuid.uuid4().hex[:10]}"
    conn = connect(schema="public")
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.execute(f'SET search_path TO "{schema}"')
    conn.commit()
    try:
        frame = pd.DataFrame(
            {
                "segment": ["United Kingdom", None],
                "contribution": [386371.2, float("nan")],
                "folds": [49, 7],
                "is_complete": [True, False],
                "period_start": [dt.date(2011, 11, 1), dt.date(2011, 10, 1)],
            }
        )
        assert write_report_table(conn, "rpt_example", frame) == 2
        types = dict(
            conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'rpt_example'",
                (schema,),
            ).fetchall()
        )
        assert types == {
            "segment": "text",
            "contribution": "double precision",
            "folds": "bigint",
            "is_complete": "boolean",
            "period_start": "date",
        }
        rows = conn.execute("SELECT * FROM rpt_example ORDER BY folds DESC").fetchall()
        assert rows == [
            ("United Kingdom", 386371.2, 49, True, dt.date(2011, 11, 1)),
            (None, None, 7, False, dt.date(2011, 10, 1)),
        ]
        assert write_report_table(conn, "rpt_example", frame.head(1)) == 1, "a rerun replaces the table"
        assert conn.execute("SELECT COUNT(*) FROM rpt_example").fetchone()[0] == 1
    finally:
        conn.rollback()
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.commit()
        conn.close()
