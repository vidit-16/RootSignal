"""The Spark conform job produces the tables the pandas adapter produces.

jobs/conform_online_retail.py is a second implementation of
rootsignal.adapters.online_retail.adapt, so it is held to the first one table by
table. The raw lines below plant every case the adapter handles: returns by
invoice prefix and by negative quantity, a missing customer, a product repeated
on one invoice, a zero price, descriptions that are missing, blank, quoted,
multi-line or padded, and a revenue that lands exactly on a half cent.

Spark needs a Java runtime, and on Windows a Hadoop helper binary as well, so
these tests run where Spark can: in CI and in the project's container.
"""

from __future__ import annotations

import importlib.util
import os
import shutil

import numpy as np
import pandas as pd
import pytest

pyspark = pytest.importorskip("pyspark")

if shutil.which("java") is None and not os.environ.get("JAVA_HOME"):
    pytest.skip("Spark needs a Java runtime", allow_module_level=True)
if os.name == "nt" and not os.environ.get("HADOOP_HOME"):
    pytest.skip("Spark cannot write files on Windows without HADOOP_HOME", allow_module_level=True)

from rootsignal.adapters.online_retail import adapt, returns_frame  # noqa: E402
from rootsignal.pipeline import CURATED, RAW, Lake, read_curated, write_raw_csv  # noqa: E402
from rootsignal.pipeline.stages import JOB_PATH, RAW_CSV  # noqa: E402

KEYS = {
    "fact_sales": ["order_id", "sku_id", "sales_type"],
    "dim_date": ["date"],
    "dim_sku": ["sku_id"],
    "dim_customer": ["customer_id"],
    "dim_region": ["region_code"],
    "dim_kam": ["kam_id"],
}


def raw_lines(repeat_a_product: bool = True) -> pd.DataFrame:
    """Invoice lines shaped as pandas reads them from the workbook."""
    rows = [
        # Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country
        (489434, 85048, "15CM CHRISTMAS GLASS BALL 20 LIGHTS", 12, "2009-12-01 07:45", 6.95, 13085.0, "United Kingdom"),
        (489434, "79323P", "PINK CHERRY LIGHTS", 12, "2009-12-01 07:45", 6.75, 13085.0, "United Kingdom"),
        (489434, "79323W", '  white "cherry" lights, boxed', 12, "2009-12-01 07:45", 6.75, 13085.0, "United Kingdom"),
        (489435, 22350, "CAT BOWL\nwith lid", 12, "2009-12-01 07:46", 2.55, np.nan, "France"),
        (489435, 22349, None, 24, "2009-12-01 07:46", 3.75, np.nan, "France"),
        (489435, 22195, "   ", 1, "2009-12-02 09:00", 0.0, np.nan, "France"),
        (489436, 21755, "LOVE BUILDING BLOCK WORD", 2, "2009-12-02 10:03", 0.5025, 13078.0, "Germany"),
        (489436, 85048, "15CM CHRISTMAS GLASS BALL 20 LIGHTS", 6, "2009-12-02 10:03", 7.95, 13078.0, "Germany"),
        ("C489449", 22087, "PAPER BUNTING WHITE LACE", -12, "2009-12-03 10:33", 2.95, 16321.0, "Australia"),
        (489450, 21755, "LOVE BUILDING BLOCK WORD", -2, "2009-12-03 11:00", 5.95, 13078.0, "Germany"),
        (489451, "POST", "POSTAGE", 1, "2009-12-04 12:00", 18.0, 12682.0, "France"),
        # A credit note with a positive quantity: a return by its prefix alone.
        ("C489460", 22423, "REGENCY CAKESTAND 3 TIER", 1, "2009-12-05 09:00", 12.75, 12682.0, "France"),
        # 22195 already sold at a zero price above; its list price must ignore it.
        (489461, 22195, "HEART MEASURING SPOONS", 3, "2009-12-05 10:00", 1.65, 12682.0, "France"),
        # A trailing space on a code that is the same product without it.
        (489463, "47503J ", "SET/3 FLORAL GARDEN TOOLS IN BAG", 1, "2009-12-05 12:00", 3.75, 13085.0, "United Kingdom"),
        # A non-breaking space before the first word, which Python's split()
        # treats as whitespace and an ASCII-only regex would not.
        (489464, 20725, "\xa0LUNCH BAG RED RETROSPOT", 2, "2009-12-05 13:00", 1.65, 13085.0, "United Kingdom"),
        # A zero quantity is a sale of nothing, not a return.
        (489465, 21212, "PACK OF 72 RETROSPOT CAKE CASES", 0, "2009-12-05 14:00", 0.55, 13085.0, "United Kingdom"),
    ]
    if repeat_a_product:
        # The same product twice on one invoice, at a different quantity and price.
        rows.append((489434, 85048, "15CM CHRISTMAS GLASS BALL 20 LIGHTS", 5, "2009-12-01 07:45", 6.45, 13085.0, "United Kingdom"))
        # The same product on one invoice under two cases of its code.
        rows.append((489462, "15056bl", "EDWARDIAN PARASOL BLACK", 2, "2009-12-05 11:00", 5.95, 13085.0, "United Kingdom"))
        rows.append((489462, "15056BL", "EDWARDIAN PARASOL BLACK", 3, "2009-12-05 11:00", 5.95, 13085.0, "United Kingdom"))

    frame = pd.DataFrame(
        rows,
        columns=["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"],
    )
    frame["InvoiceDate"] = pd.to_datetime(frame["InvoiceDate"])
    return frame


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[1]")
        .appName("rootsignal-tests")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def load_job():
    spec = importlib.util.spec_from_file_location("conform_online_retail", JOB_PATH)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    return job


def run_job(spark, raw: pd.DataFrame, tmp_path):
    """Land the lines as the pipeline does, run the job's entry point, read its output.

    main() picks up the test's session through getOrCreate, so this is the same
    code path spark-submit and Glue run.
    """
    lake = Lake(str(tmp_path / "lake"))
    write_raw_csv(raw, lake.uri(RAW, RAW_CSV))
    load_job().main(["--input", lake.uri(RAW, RAW_CSV), "--output", lake.uri(CURATED)])
    return read_curated(lake)


def assert_tables_match(spark_table: pd.DataFrame, pandas_table: pd.DataFrame, key: list[str]) -> None:
    assert sorted(spark_table.columns) == sorted(pandas_table.columns)
    left = spark_table.sort_values(key).reset_index(drop=True)
    right = pandas_table[list(spark_table.columns)].sort_values(key).reset_index(drop=True)
    assert len(left) == len(right)
    for column in left.columns:
        a, b = left[column], right[column]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) and a.dtype != bool:
            np.testing.assert_allclose(
                a.astype(float), b.astype(float), rtol=0, atol=1e-9, equal_nan=True, err_msg=column
            )
        else:
            assert a.astype(str).tolist() == b.astype(str).tolist(), column



# Each Spark run takes tens of seconds, so each fixture is conformed once and
# shared by the tests that read it.


@pytest.fixture(scope="module")
def conformed(spark, tmp_path_factory):
    raw = raw_lines()
    dataset, returns = run_job(spark, raw, tmp_path_factory.mktemp("with_repeats"))
    return raw, dataset, returns


@pytest.fixture(scope="module")
def conformed_without_repeats(spark, tmp_path_factory):
    raw = raw_lines(repeat_a_product=False)
    dataset, _ = run_job(spark, raw, tmp_path_factory.mktemp("without_repeats"))
    return raw, dataset


@pytest.mark.parametrize("table", list(KEYS))
def test_spark_matches_the_pandas_adapter(conformed, table) -> None:
    raw, spark_dataset, _ = conformed
    assert_tables_match(spark_dataset.tables[table], adapt(raw=raw).tables[table], KEYS[table])


def test_without_repeated_lines_the_published_price_is_kept(conformed_without_repeats) -> None:
    """The adapter only recomputes prices when it had lines to combine."""
    raw, spark_dataset = conformed_without_repeats
    sales = spark_dataset.tables["fact_sales"]
    assert sorted(sales["unit_price"].tolist()) == sorted(
        raw.loc[(raw["Quantity"] >= 0) & ~raw["Invoice"].astype(str).str.startswith("C"), "Price"].tolist()
    )
    assert_tables_match(sales, adapt(raw=raw).tables["fact_sales"], KEYS["fact_sales"])
    assert "0 invoice lines repeat a product" in " ".join(spark_dataset.capabilities.notes)


def test_half_cent_revenue_rounds_the_way_pandas_rounds(spark, conformed) -> None:
    """2 x 0.5025 is 1.005. pandas gives 1.0; Spark's own round() gives 1.01."""
    from pyspark.sql import functions as F

    assert spark.range(1).select(F.round(F.lit(2) * F.lit(0.5025), 2)).first()[0] == pytest.approx(1.01)

    _, spark_dataset, _ = conformed
    sales = spark_dataset.tables["fact_sales"].set_index(["order_id", "sku_id"])
    assert sales.loc[("489436", "21755"), "net_sales"] == pytest.approx(1.0)


def test_returns_are_split_out_the_same_way(conformed) -> None:
    raw, _, spark_returns = conformed
    expected = returns_frame(raw=raw)
    assert sorted(spark_returns["order_id"]) == sorted(expected["order_id"])
    assert spark_returns["returned_units"].sum() == expected["returned_units"].sum()


def test_summary_counts_what_the_job_did(conformed) -> None:
    _, spark_dataset, _ = conformed
    assert spark_dataset.source_rows == 19
    notes = " ".join(spark_dataset.capabilities.notes)
    assert "3 of 19 lines are returns" in notes
    assert "2 invoice lines repeat a product" in notes


def test_a_zero_quantity_is_a_sale_of_nothing_not_a_return(conformed) -> None:
    _, spark_dataset, spark_returns = conformed
    sales = spark_dataset.tables["fact_sales"].set_index(["order_id", "sku_id"])
    assert sales.loc[("489465", "21212"), "units"] == 0
    assert "489465" not in set(spark_returns["order_id"])


def test_the_first_value_follows_the_file_not_the_arrival_order(spark) -> None:
    """pandas' groupby 'first' is the first non-null value in file order.

    On one partition, rows arrive in file order anyway, so the fixture above
    cannot tell file order from arrival order. Here the rows arrive in a
    different order from their position in the file, as they can on a cluster.
    """
    job = load_job()
    rows = [("k", "arrived first, later in the file", 5), ("k", None, 0), ("k", "earliest non-null", 1)]
    frame = spark.createDataFrame(rows, "key string, value string, _row long").coalesce(1)
    picked = frame.groupBy("key").agg(job.first_in_file_order("value").alias("value")).first()["value"]
    assert picked == "earliest non-null"


def test_the_job_runs_from_the_command_line(tmp_path) -> None:
    """The entry point spark-submit, Glue and EMR use, run as a separate process."""
    from rootsignal.pipeline import conform_locally, read_summary

    lake = Lake(str(tmp_path / "lake"))
    write_raw_csv(raw_lines(), lake.uri(RAW, RAW_CSV))
    summary = conform_locally(lake)
    assert summary == read_summary(lake)
    assert summary["source_rows"] == 19 and summary["returned_lines"] == 3
