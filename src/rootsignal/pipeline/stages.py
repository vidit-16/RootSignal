"""The stages that take Online Retail II from its source to the warehouse.

    land     the published workbook into the raw zone, with a CSV rendering of
             it, because Spark has no Excel reader
    conform  jobs/conform_online_retail.py, in Spark: raw zone to curated zone
    load     curated Parquet through the existing cleaning and validation, into
             PostgreSQL and the clean zone; nothing is loaded if validation
             still finds an error
    publish  the analysis results, written to the warehouse as report tables

Cleaning stays in pandas on purpose. It is the part of the project with the
audit trail and the quarantine, it runs in seconds on the conformed tables, and
rewriting it for Spark would create a second definition of every rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..adapters.base import AdaptedDataset, DatasetCapabilities, empty_fact
from ..adapters.online_retail import ARCHIVE_NAME, CAPABILITIES, DEFAULT_CACHE, download, read_raw
from .lake import CLEAN, CURATED, JOBS, RAW, Lake

JOB_PATH = Path(__file__).resolve().parents[3] / "jobs" / "conform_online_retail.py"
RAW_CSV = "invoices.csv"
CURATED_TABLES = ("fact_sales", "dim_date", "dim_sku", "dim_customer", "dim_region", "dim_kam")
EMPTY_FACTS = ("fact_orders", "fact_inventory", "fact_targets", "fact_kam_targets")
DATE_COLUMNS = {"fact_sales": ["date"], "dim_date": ["date"], "returns": ["date"]}


def write_raw_csv(raw: pd.DataFrame, path: str | Path) -> Path:
    """The workbook's rows as CSV, values unchanged.

    Numbers are written at full precision and a missing value as an empty
    field, which the Spark job reads back as null. Nothing is cleaned here.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(path, index=False)
    return path


def land(lake: Lake, cache_dir: str | Path = DEFAULT_CACHE) -> dict:
    """Put the source into the raw zone: the archive as published, and a CSV."""
    archive = download(cache_dir)
    raw = read_raw(cache_dir)
    lake.put(archive, RAW, ARCHIVE_NAME)
    if lake.is_s3:
        with tempfile.TemporaryDirectory() as scratch:
            lake.put(write_raw_csv(raw, Path(scratch, RAW_CSV)), RAW, RAW_CSV)
    else:
        write_raw_csv(raw, Path(lake.uri(RAW, RAW_CSV)))
    return {"raw_rows": len(raw), "raw_csv": lake.uri(RAW, RAW_CSV)}


def publish_job(lake: Lake) -> str:
    """Put the Spark script where Glue or EMR can fetch it."""
    return lake.put(JOB_PATH, JOBS, JOB_PATH.name)


def conform_locally(lake: Lake) -> dict:
    """Run the Spark job on this machine against a local lake."""
    if lake.is_s3:
        raise ValueError("Run the conform stage on AWS for an S3 lake; see rootsignal.pipeline.aws.")
    completed = subprocess.run(
        [sys.executable, str(JOB_PATH), "--input", lake.uri(RAW, RAW_CSV), "--output", lake.uri(CURATED)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"The Spark job failed:\n{completed.stderr[-4000:]}")
    return read_summary(lake)


def read_summary(lake: Lake, into: str | Path | None = None) -> dict:
    """The job's own record of what it read and wrote."""
    with tempfile.TemporaryDirectory() as scratch:
        folder = lake.fetch(CURATED, "_summary", into=into or scratch)
        text = "".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("part-*")))
    return json.loads(text)


def _read_table(folder: Path, name: str) -> pd.DataFrame:
    frame = pd.read_parquet(folder / name)
    for column in DATE_COLUMNS.get(name, []):
        frame[column] = pd.to_datetime(frame[column]).dt.date
    return frame


def read_curated(
    lake: Lake, into: str | Path | None = None, zone: str = CURATED
) -> tuple[AdaptedDataset, pd.DataFrame]:
    """The conformed tables as the pandas adapter would return them, and the returns.

    The facts this dataset cannot supply are added empty, as the adapter adds
    them, so cleaning and validation see the same shape either way. `zone` reads
    a Spark run written somewhere other than the curated zone.
    """
    scratch = tempfile.TemporaryDirectory() if into is None else None
    try:
        folder = lake.fetch(zone, into=into or scratch.name)
        tables = {name: _read_table(folder, name) for name in CURATED_TABLES}
        returns = _read_table(folder, "returns")
        summary = json.loads(
            "".join(path.read_text(encoding="utf-8") for path in sorted((folder / "_summary").glob("part-*")))
        )
    finally:
        if scratch is not None:
            scratch.cleanup()

    for name in EMPTY_FACTS:
        tables[name] = empty_fact(name)

    notes = CAPABILITIES.notes + (
        f"{summary['returned_lines']:,} of {summary['source_rows']:,} lines are returns and are "
        "held separately from sales.",
        f"{summary['consolidated_lines']:,} invoice lines repeat a product already on the same "
        "invoice and are consolidated into one, adding units and weighting the price by quantity.",
        "Conformed in Spark by jobs/conform_online_retail.py.",
    )
    dataset = AdaptedDataset(
        tables=tables,
        capabilities=DatasetCapabilities(
            name=CAPABILITIES.name, available_facts=CAPABILITIES.available_facts, notes=notes
        ),
        source_rows=summary["source_rows"],
    )
    return dataset, returns


@dataclass
class LoadReport:
    loaded: dict[str, int]
    quarantined: int
    errors_before_cleaning: int
    audit: pd.DataFrame = field(repr=False)
    tables: dict[str, pd.DataFrame] = field(repr=False, default_factory=dict)


def clean(dataset: AdaptedDataset):
    """Clean and validate. Any error left after cleaning stops the pipeline."""
    from ..cleaning import clean_dataset
    from ..validation import DatasetValidator

    before = DatasetValidator().validate(dataset.tables)
    result = clean_dataset(dataset.tables)
    after = DatasetValidator().validate(result.tables)
    if after.errors():
        problems = "; ".join(f"{issue.table}.{issue.check}: {issue.message}" for issue in after.errors())
        raise ValueError(f"Not loading: validation still fails after cleaning. {problems}")
    return result, len(before.errors())


def write_clean_zone(lake: Lake, tables: dict[str, pd.DataFrame]) -> dict[str, str]:
    """The cleaned model as Parquet, typed exactly as schema.sql declares it.

    This is what Redshift loads with COPY, so the types have to match the
    tables: 32-bit integers for INTEGER, dates for DATE, 0/1 for flags.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..sql.database import LOAD_ORDER, SCHEMA_PATH
    from ..sql.dialect import schema_columns

    arrow_types = {"TEXT": pa.string(), "INTEGER": pa.int32(), "REAL": pa.float64(), "DATE": pa.date32()}
    declared = schema_columns(SCHEMA_PATH.read_text(encoding="utf-8"))
    written = {}
    with tempfile.TemporaryDirectory() as scratch:
        for name in LOAD_ORDER:
            frame = tables[name]
            columns = declared[name]
            arrays = []
            for column, kind in columns:
                values = frame[column]
                if kind == "INTEGER":
                    values = values.astype("int64")
                arrays.append(pa.array(values.tolist(), type=arrow_types[kind]))
            table = pa.Table.from_arrays(arrays, names=[column for column, _ in columns])
            local = Path(scratch, f"{name}.parquet")
            pq.write_table(table, local)
            written[name] = lake.put(local, CLEAN, name, "part-00000.parquet")
    return written


def load(
    dataset: AdaptedDataset,
    url: str | None = None,
    schema: str = "rootsignal",
    lake: Lake | None = None,
) -> LoadReport:
    """Clean, validate, and replace the warehouse schema with the result.

    Validation runs again after cleaning, and any remaining error stops the load
    before the warehouse is touched. With a lake, the cleaned tables are also
    written to its clean zone for Redshift.
    """
    from ..sql.warehouse import build_warehouse

    result, errors_before = clean(dataset)
    connection = build_warehouse(result.tables, url=url, schema=schema)
    connection.close()
    if lake is not None:
        write_clean_zone(lake, result.tables)
    return LoadReport(
        loaded={name: len(frame) for name, frame in result.tables.items()},
        quarantined=sum(len(frame) for frame in result.quarantined.values()),
        errors_before_cleaning=errors_before,
        audit=result.audit,
        tables=result.tables,
    )


def publish(
    tables: dict[str, pd.DataFrame],
    returns: pd.DataFrame,
    url: str | None = None,
    schema: str = "rootsignal",
) -> dict[str, int]:
    """Write the analysis results into the warehouse as report tables."""
    from ..sql.warehouse import connect, write_report_table
    from .publish import analysis_tables, flatten

    connection = connect(url, schema)
    try:
        return {
            name: write_report_table(connection, name, flatten(frame))
            for name, frame in analysis_tables(tables, returns).items()
        }
    finally:
        connection.close()
