"""Conform raw Online Retail II invoice lines into the RootSignal model, in Spark.

This is the Spark form of rootsignal.adapters.online_retail.adapt. It reads the
raw zone, applies the same mapping, and writes one Parquet dataset per table to
the curated zone. Cleaning, validation and the warehouse load run after it, on
its output, exactly as they run on the pandas adapter's.

It is deliberately a single file that imports nothing from the rootsignal
package, so the same script runs under spark-submit on a laptop, as an AWS Glue
job and on EMR Serverless without packaging the project. It also keeps to
Python 3.9 syntax, which is what EMR Serverless runs.

Every rule matches the pandas adapter, down to how numbers are rounded:
pandas rounds by scaling, rounding half to even and scaling back, so that is
what rint(x * 100) / 100 does here. Spark's own round() rounds the decimal
value half up, and gives a different cent on an exact half cent. On Online
Retail II that never happens: prices carry at most two decimals and quantities
are whole, and scripts/compare_spark_to_pandas.py counts 0 of 1,044,420 sales
lines affected. The job rounds the pandas way regardless, so the two stay
equal on data where it does happen; the test fixture includes such a line.
tests/test_spark_conform.py asserts the two produce the same tables.

    spark-submit jobs/conform_online_retail.py --input <raw prefix> --output <curated prefix>
"""

from __future__ import annotations

import argparse
import json
import sys

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

RAW_SCHEMA = T.StructType(
    [
        T.StructField("Invoice", T.StringType()),
        T.StructField("StockCode", T.StringType()),
        T.StructField("Description", T.StringType()),
        T.StructField("Quantity", T.StringType()),
        T.StructField("InvoiceDate", T.StringType()),
        T.StructField("Price", T.StringType()),
        T.StructField("Customer ID", T.StringType()),
        T.StructField("Country", T.StringType()),
    ]
)

FACT_SALES_COLUMNS = [
    "order_id", "date", "customer_id", "region_code", "channel", "kam_id",
    "sku_id", "category", "sales_type", "units", "unit_price", "discount_pct", "net_sales",
]
SALES_KEY = ["order_id", "sku_id", "sales_type"]

# Files written per table. Left alone, Spark writes one file per shuffle
# partition, 200 by default: 200 files of about 100 KB for a million sales lines,
# which every reader, Redshift's COPY included, then has to open one by one.
OUTPUT_FILES = {"fact_sales": 4}


def round_half_even(column, places: int):
    """NumPy's rounding: scale, round half to even, scale back."""
    scale = float(10**places)
    return F.rint(column * F.lit(scale)) / F.lit(scale)


def first_in_file_order(column: str):
    """pandas groupby 'first': the first non-null value, in source row order."""
    return F.min_by(F.col(column), F.when(F.col(column).isNotNull(), F.col("_row")))


def read_raw(spark: SparkSession, path: str) -> DataFrame:
    """Invoice lines as published, every value read as text and typed explicitly.

    multiLine keeps a quoted description containing a line break as one row, and
    reads the file in one pass, so _row follows the order of lines in the file.
    """
    raw = (
        spark.read.option("header", True)
        .option("multiLine", True)
        .option("escape", '"')
        .schema(RAW_SCHEMA)
        .csv(path)
        .withColumn("_row", F.monotonically_increasing_id())
    )
    return raw.select(
        F.col("_row"),
        F.col("Invoice").alias("order_id"),
        # The same product appears as 15056BL and 15056bl, and as 47503J with
        # and without a trailing space. Trimmed as Python's str.strip() trims,
        # any Unicode whitespace, then upper-cased.
        F.upper(F.regexp_replace("StockCode", r"(?U)^\s+|\s+$", "")).alias("sku_id"),
        F.col("Description").alias("sku_name"),
        F.col("Quantity").cast("long").alias("units"),
        F.to_timestamp("InvoiceDate").alias("timestamp"),
        F.col("Price").cast("double").alias("unit_price"),
        F.col("Customer ID").alias("customer_id_raw"),
        F.col("Country").alias("region_code"),
    )


def conform_lines(raw: DataFrame) -> DataFrame:
    """Derived columns on every line, before sales and returns are separated."""
    first_word = F.regexp_extract(F.upper(F.coalesce(F.col("sku_name"), F.lit("UNKNOWN"))), r"(?U)^\s*(\S+)", 1)
    return (
        raw.withColumn("date", F.to_date("timestamp"))
        .withColumn("category", F.when(first_word == "", "UNKNOWN").otherwise(first_word))
        # A fifth of lines have no customer. They are named rather than dropped.
        .withColumn(
            "customer_id",
            F.coalesce(F.regexp_replace("customer_id_raw", r"\.0$", ""), F.lit("UNKNOWN")),
        )
        .withColumn("channel", F.lit("Online"))
        .withColumn("sales_type", F.lit("PRIMARY"))
        .withColumn("kam_id", F.lit("UNASSIGNED"))
        .withColumn(
            "is_return",
            (F.col("units") < 0) | F.upper(F.col("order_id")).startswith("C"),
        )
    )


def consolidate_repeated_lines(sales: DataFrame) -> tuple[DataFrame, int]:
    """Combine lines repeating a product on the same invoice.

    Units and revenue are added and the price follows from them, so volume and
    revenue are both unchanged. As in the pandas adapter, the price is
    recomputed on every line whenever any line was combined.
    """
    total = sales.count()
    distinct = sales.select(*SALES_KEY).distinct().count()
    repeated = total - distinct
    if repeated == 0:
        return sales.select(*FACT_SALES_COLUMNS), 0

    grouped = sales.groupBy(*SALES_KEY).agg(
        *(first_in_file_order(name).alias(name)
          for name in ("date", "customer_id", "region_code", "channel", "kam_id", "category", "discount_pct")),
        F.sum("units").alias("units"),
        F.sum("net_sales").alias("net_sales"),
    )
    grouped = grouped.withColumn(
        "unit_price",
        round_half_even(
            F.col("net_sales") / F.when(F.col("units") != 0, F.col("units").cast("double")), 4
        ),
    )
    return grouped.select(*FACT_SALES_COLUMNS), repeated


def build_dim_date(fact_sales: DataFrame) -> DataFrame:
    return (
        fact_sales.select("date").where(F.col("date").isNotNull()).distinct()
        .select(
            "date",
            F.weekofyear("date").alias("week"),
            F.month("date").alias("month"),
            F.concat(F.lit("Q"), F.quarter("date").cast("string")).alias("quarter"),
            F.year("date").alias("year"),
        )
    )


def build_dim_sku(sales: DataFrame) -> DataFrame:
    # Price comes from lines with a real one; samples and adjustments sit at or
    # below zero and would drag a median negative.
    firsts = sales.groupBy("sku_id").agg(
        first_in_file_order("sku_name").alias("sku_name"),
        first_in_file_order("category").alias("category"),
    )
    medians = (
        sales.where(F.col("unit_price") > 0)
        .groupBy("sku_id")
        .agg(F.expr("percentile(unit_price, 0.5)").alias("list_price"))
    )
    sku = firsts.join(medians, "sku_id", "left").fillna({"list_price": 0.0})
    return sku.select(
        "sku_id",
        F.coalesce("sku_name", F.lit("Not recorded")).alias("sku_name"),
        "category",
        F.lit("Standard").alias("sub_category"),
        F.lit(1.0).alias("pack_size_kg"),
        round_half_even(F.col("list_price") * F.lit(0.7), 4).alias("unit_cost"),
        "list_price",
        F.lit(True).alias("active_flag"),
    )


def build_dim_customer(sales: DataFrame) -> DataFrame:
    customer = sales.groupBy("customer_id").agg(first_in_file_order("region_code").alias("region_code"))
    return customer.select(
        "customer_id",
        F.when(F.col("customer_id") == "UNKNOWN", F.lit("Unidentified customer"))
        .otherwise(F.concat(F.lit("Customer "), F.col("customer_id")))
        .alias("customer_name"),
        F.lit("Retail").alias("customer_type"),
        F.lit("Online").alias("channel"),
        "region_code",
        F.col("region_code").alias("city"),
        F.col("region_code").alias("zone"),
        F.lit("UNASSIGNED").alias("kam_id"),
    )


def build_dim_region(sales: DataFrame) -> DataFrame:
    regions = sales.select("region_code").where(F.col("region_code").isNotNull()).distinct()
    return regions.select("region_code", F.col("region_code").alias("city"), F.col("region_code").alias("zone"))


def conform(spark: SparkSession, input_path: str) -> tuple[dict, dict]:
    """Every curated table, plus a summary of what the run did."""
    raw = read_raw(spark, input_path)
    missing_keys = raw.where(F.col("order_id").isNull() | F.col("sku_id").isNull()).count()
    if missing_keys:
        raise ValueError(f"{missing_keys} raw line(s) have no invoice or stock code.")

    lines = conform_lines(raw).cache()
    sales = (
        lines.where(~F.col("is_return"))
        .withColumn("discount_pct", F.lit(0.0))
        .withColumn("net_sales", round_half_even(F.col("units") * F.col("unit_price"), 2))
        .cache()
    )
    returns = lines.where(F.col("is_return")).select(
        "order_id", "sku_id", "region_code", "customer_id", "date", "units",
        F.abs("units").alias("returned_units"),
    )

    fact_sales, repeated = consolidate_repeated_lines(sales)
    fact_sales = fact_sales.cache()

    tables = {
        "fact_sales": fact_sales,
        "dim_date": build_dim_date(fact_sales),
        "dim_sku": build_dim_sku(sales),
        "dim_customer": build_dim_customer(sales),
        "dim_region": build_dim_region(sales),
        "dim_kam": spark.createDataFrame([("UNASSIGNED", "Not recorded")], "kam_id string, kam_name string"),
        "returns": returns,
    }
    summary = {
        "source_rows": lines.count(),
        "returned_lines": returns.count(),
        "consolidated_lines": repeated,
        "tables": {name: frame.count() for name, frame in tables.items()},
    }
    return tables, summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="Raw invoice CSV file or prefix.")
    parser.add_argument("--output", required=True, help="Curated prefix; one Parquet dataset per table.")
    # Glue passes its own arguments alongside these.
    args, _ = parser.parse_known_args(argv)

    spark = (
        SparkSession.builder.appName("rootsignal-conform-online-retail")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    tables, summary = conform(spark, args.input)
    output = args.output.rstrip("/")
    for name, frame in tables.items():
        frame.coalesce(OUTPUT_FILES.get(name, 1)).write.mode("overwrite").parquet(f"{output}/{name}")
    spark.createDataFrame([(json.dumps(summary),)], "summary string").coalesce(1).write.mode(
        "overwrite"
    ).text(f"{output}/_summary")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
