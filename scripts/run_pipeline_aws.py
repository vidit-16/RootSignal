"""Run the pipeline on AWS: S3, Glue, EMR Serverless and Redshift Serverless.

Each step is a subcommand, so a run can be stopped, inspected and resumed, and
nothing that bills is created without being asked for by name:

    python scripts/run_pipeline_aws.py setup        bucket and IAM roles (free)
    python scripts/run_pipeline_aws.py land         raw zone to S3 (free tier)
    python scripts/run_pipeline_aws.py glue         conform in Glue           (bills)
    python scripts/run_pipeline_aws.py emr          conform on EMR Serverless (bills)
    python scripts/run_pipeline_aws.py load         clean, into PostgreSQL and the S3 clean zone
    python scripts/run_pipeline_aws.py redshift     load Redshift and check it (bills)
    python scripts/run_pipeline_aws.py status       everything that can bill
    python scripts/run_pipeline_aws.py teardown     remove everything that can bill

Credentials come from the AWS CLI configuration, as for any boto3 program.
"""

from __future__ import annotations

import argparse
import tempfile
import time

import numpy as np
import pandas as pd

from rootsignal.console import use_utf8_output
from rootsignal.pipeline import Lake, land, load, publish, read_curated
from rootsignal.pipeline import aws

EMR_OUTPUT = "curated_emr/online_retail"


def lake_for(bucket: str | None) -> Lake:
    return Lake(f"s3://{bucket or aws.default_bucket()}")


def roles(bucket: str) -> dict[str, str]:
    return {
        "glue": aws.ensure_role(
            aws.GLUE_ROLE,
            ["glue.amazonaws.com"],
            bucket,
            write=True,
            managed=("arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole",),
        ),
        "emr": aws.ensure_role(aws.EMR_ROLE, ["emr-serverless.amazonaws.com"], bucket, write=True),
        "redshift": aws.ensure_role(
            aws.REDSHIFT_ROLE,
            ["redshift.amazonaws.com", "redshift-serverless.amazonaws.com"],
            bucket,
            write=False,
        ),
    }


def show_run(run: aws.SparkRun) -> None:
    print(f"{run.engine}: {run.state} in {run.seconds:.0f}s, {run.usage}")
    print(f"  output {run.output}")
    if run.error:
        print(f"  error  {run.error}")


def compare_frames(left: pd.DataFrame, right: pd.DataFrame, tolerance: dict[str, float]) -> list[str]:
    """Columns that differ between two results of the same query."""
    if list(left.columns) != list(right.columns):
        return [f"columns {list(left.columns)} vs {list(right.columns)}"]
    if len(left) != len(right):
        return [f"{len(left)} rows vs {len(right)}"]
    problems = []
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    for column in left.columns:
        a, b = left[column], right[column]
        numeric_a = pd.to_numeric(a, errors="coerce")
        numeric_b = pd.to_numeric(b, errors="coerce")
        if numeric_a.notna().sum() == a.notna().sum() and numeric_b.notna().sum() == b.notna().sum():
            x, y = numeric_a.astype(float).to_numpy(), numeric_b.astype(float).to_numpy()
            gap = np.abs(np.where(np.isnan(x) & np.isnan(y), 0.0, x - y))
            bad = int((gap > tolerance.get(column, 1e-9)).sum() + (np.isnan(x) != np.isnan(y)).sum())
        else:
            bad = int((a.astype(str).to_numpy() != b.astype(str).to_numpy()).sum())
        if bad:
            problems.append(f"{column}: {bad} row(s)")
    return problems


def check_redshift_against_sqlite(schema: str) -> int:
    """Every query on the generated sample, Redshift against SQLite."""
    from rootsignal.dataset import load_tables_for_analysis
    from rootsignal.sql import available_queries, build_database, run_query_file

    sqlite = build_database(load_tables_for_analysis("data/raw/generated"))
    failures = 0
    for name in available_queries():
        problems = compare_frames(aws.query_redshift(schema, name), run_query_file(sqlite, name), {})
        print(f"  {name:24s} {'identical' if not problems else 'DIFFERS: ' + '; '.join(problems)}")
        failures += bool(problems)
    return failures


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["setup", "land", "glue", "emr", "load", "redshift", "status", "teardown"])
    parser.add_argument("--bucket", default=None, help="Defaults to rootsignal-lake-<account>-<region>.")
    parser.add_argument("--warehouse-url", default=None, help="PostgreSQL; defaults to ROOTSIGNAL_WAREHOUSE_URL.")
    args = parser.parse_args()
    started = time.time()

    if args.step == "status":
        found = aws.billable_resources()
        print("\n".join(found) if found else "Nothing that can bill.")
        return
    if args.step == "teardown":
        removed = aws.teardown()
        print("\n".join(f"removed {item}" for item in removed) if removed else "Nothing to remove.")
        left = aws.billable_resources()
        print("Still present: " + ", ".join(left) if left else "Nothing that can bill is left.")
        return

    bucket = args.bucket or aws.default_bucket()
    lake = lake_for(bucket)

    if args.step == "setup":
        aws.ensure_bucket(bucket)
        for name, arn in roles(bucket).items():
            print(f"role {name:8s} {arn}")
        print(f"lake {lake.root}")

    elif args.step == "land":
        landed = land(lake)
        print(f"{landed['raw_rows']:,} raw lines to {landed['raw_csv']}")

    elif args.step == "glue":
        show_run(aws.run_glue(lake, roles(bucket)["glue"]))

    elif args.step == "emr":
        show_run(aws.run_emr(lake, roles(bucket)["emr"], lake.uri(EMR_OUTPUT)))
        # The same script on a second engine should write the same tables.
        with tempfile.TemporaryDirectory() as scratch:
            glue_dataset, _ = read_curated(lake, into=f"{scratch}/glue")
            emr_dataset, _ = read_curated(lake, into=f"{scratch}/emr", zone=EMR_OUTPUT)
        for name, frame in glue_dataset.tables.items():
            if frame.empty:
                continue
            key = list(frame.columns[:1]) if name != "fact_sales" else ["order_id", "sku_id", "sales_type"]
            problems = compare_frames(
                frame.sort_values(key).reset_index(drop=True),
                emr_dataset.tables[name].sort_values(key).reset_index(drop=True),
                {"net_sales": 1e-6, "unit_price": 1e-6},
            )
            print(f"  EMR vs Glue {name:14s} {'identical' if not problems else 'DIFFERS: ' + '; '.join(problems)}")

    elif args.step == "load":
        dataset, returns = read_curated(lake)
        report = load(dataset, url=args.warehouse_url, lake=lake)
        published = publish(report.tables, returns, url=args.warehouse_url)
        print(
            f"{report.errors_before_cleaning} validation errors before cleaning, "
            f"{report.quarantined:,} rows quarantined, 0 after"
        )
        print(f"PostgreSQL: {report.loaded['fact_sales']:,} sales lines, {len(published)} report tables")
        print(f"clean zone: {lake.uri('clean/online_retail')}")

    elif args.step == "redshift":
        role = roles(bucket)["redshift"]
        aws.ensure_redshift(role)
        counts = aws.load_redshift(lake, "online_retail")
        print("Redshift online_retail: " + ", ".join(f"{k} {v:,}" for k, v in counts.items() if v))

        # The generated sample fills every fact, so every query has rows to compare.
        from rootsignal.dataset import load_tables_for_analysis
        from rootsignal.pipeline import write_clean_zone

        sample = load_tables_for_analysis("data/raw/generated")
        sample_lake = Lake(f"s3://{bucket}/sample")
        write_clean_zone(sample_lake, sample)
        aws.load_redshift(sample_lake, "sample")
        print("Redshift against SQLite, every analytical query on the generated sample:")
        failures = check_redshift_against_sqlite("sample")
        print("all identical" if not failures else f"{failures} quer(ies) differ")

    print(f"\n{time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
