"""Compare the Spark conform job with the pandas adapter on the whole dataset.

tests/test_spark_conform.py holds the two to each other on a planted fixture.
This does the same on all 1,067,371 published lines, table by table, and
reports every column that differs. Run it after the conform stage:

    docker compose run --rm pipeline python scripts/run_pipeline.py --stages land,conform
    docker compose run --rm pipeline python scripts/compare_spark_to_pandas.py

It also counts the lines where Spark's built-in round() would have disagreed
with the pandas-style rounding the job uses. On this dataset the count is 0.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from rootsignal.adapters.online_retail import adapt, read_raw
from rootsignal.console import use_utf8_output
from rootsignal.pipeline import RAW, Lake, read_curated
from rootsignal.pipeline.stages import RAW_CSV

KEYS = {
    "fact_sales": ["order_id", "sku_id", "sales_type"],
    "dim_date": ["date"],
    "dim_sku": ["sku_id"],
    "dim_customer": ["customer_id"],
    "dim_region": ["region_code"],
    "dim_kam": ["kam_id"],
}


def compare(spark_table: pd.DataFrame, pandas_table: pd.DataFrame, key: list[str]) -> list[str]:
    """Every difference between two tables, as readable lines. Empty when equal."""
    problems = []
    if sorted(spark_table.columns) != sorted(pandas_table.columns):
        return [f"columns differ: {sorted(spark_table.columns)} vs {sorted(pandas_table.columns)}"]
    if len(spark_table) != len(pandas_table):
        problems.append(f"row count {len(spark_table):,} vs {len(pandas_table):,}")

    merged = spark_table.merge(
        pandas_table, on=key, how="outer", suffixes=("_spark", "_pandas"), indicator=True
    )
    unmatched = merged["_merge"] != "both"
    if unmatched.any():
        problems.append(f"{int(unmatched.sum()):,} key(s) present on one side only")
    both = merged.loc[~unmatched]

    for column in spark_table.columns:
        if column in key:
            continue
        a, b = both[f"{column}_spark"], both[f"{column}_pandas"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) and a.dtype != bool:
            x, y = a.astype(float).to_numpy(), b.astype(float).to_numpy()
            gap = np.abs(np.where(np.isnan(x) & np.isnan(y), 0.0, x - y))
            differing = int((gap > 1e-9).sum() + (np.isnan(x) != np.isnan(y)).sum())
            if differing:
                problems.append(f"{column}: {differing:,} row(s) differ, largest gap {np.nanmax(gap):.3g}")
        else:
            differing = int((a.astype(str).to_numpy() != b.astype(str).to_numpy()).sum())
            if differing:
                problems.append(f"{column}: {differing:,} row(s) differ")
    return problems


def spark_round_disagreements(raw_csv: str) -> tuple[int, int]:
    """Sales lines where Spark's own round() would have given different revenue.

    Counted in Spark, on the job's own reading of the raw zone: round() against
    the half-to-even rounding the job uses to match pandas.
    """
    import importlib.util

    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    from rootsignal.pipeline.stages import JOB_PATH

    spec = importlib.util.spec_from_file_location("conform_online_retail", JOB_PATH)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)

    spark = SparkSession.builder.appName("round-check").getOrCreate()
    sales = job.conform_lines(job.read_raw(spark, raw_csv)).where(~F.col("is_return"))
    revenue = F.col("units") * F.col("unit_price")
    total = sales.count()
    differing = sales.where(F.round(revenue, 2) != job.round_half_even(revenue, 2)).count()
    spark.stop()
    return differing, total


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lake", default="data/lake")
    args = parser.parse_args()

    started = time.time()
    spark_dataset, _ = read_curated(Lake(args.lake))
    raw = read_raw()
    pandas_dataset = adapt(raw=raw)
    print(f"{len(raw):,} published lines; both implementations read in {time.time() - started:.0f}s\n")

    failures = 0
    for name, key in KEYS.items():
        problems = compare(spark_dataset.tables[name], pandas_dataset.tables[name], key)
        rows = len(spark_dataset.tables[name])
        print(f"{name:14s} {rows:>10,} rows  {'identical' if not problems else 'DIFFERS'}")
        for problem in problems:
            print(f"               {problem}")
        failures += bool(problems)

    disagree, lines = spark_round_disagreements(Lake(args.lake).uri(RAW, RAW_CSV))
    print(
        f"\nSpark's round() would have changed the revenue on {disagree:,} of {lines:,} sales lines "
        f"({disagree / lines:.2%})."
    )
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
