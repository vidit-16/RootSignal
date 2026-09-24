"""Run the Online Retail II pipeline: land, conform in Spark, load, publish.

Locally, the lake is a folder and Spark runs on this machine:

    docker compose up -d warehouse
    docker compose run --rm pipeline

On AWS the lake is an S3 bucket and the conform step runs as a Glue job; see
scripts/run_pipeline_aws.py.
"""

from __future__ import annotations

import argparse
import time

from rootsignal.console import use_utf8_output
from rootsignal.pipeline import Lake, conform_locally, land, load, publish, read_curated

STAGES = ("land", "conform", "load", "publish")


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lake", default="data/lake", help="Local folder for the raw and curated zones.")
    parser.add_argument("--warehouse-url", default=None, help="Defaults to ROOTSIGNAL_WAREHOUSE_URL.")
    parser.add_argument("--schema", default="rootsignal")
    parser.add_argument("--stages", default=",".join(STAGES), help="Comma-separated subset of land,conform,load,publish.")
    args = parser.parse_args()

    stages = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    unknown = sorted(set(stages) - set(STAGES))
    if unknown:
        parser.error(f"Unknown stage(s): {unknown}")

    lake = Lake(args.lake)
    started = time.time()

    if "land" in stages:
        landed = land(lake)
        print(f"land     {landed['raw_rows']:,} raw lines to {landed['raw_csv']}")

    if "conform" in stages:
        summary = conform_locally(lake)
        print(
            f"conform  {summary['source_rows']:,} lines in, {summary['returned_lines']:,} returns held apart, "
            f"{summary['consolidated_lines']:,} repeated lines combined"
        )
        for name, rows in summary["tables"].items():
            print(f"         {name:14s} {rows:>10,}")

    if "load" in stages or "publish" in stages:
        dataset, returns = read_curated(lake)
        report = load(dataset, url=args.warehouse_url, schema=args.schema, lake=lake)
        print(
            f"load     {report.errors_before_cleaning} validation error(s) before cleaning, "
            f"{report.quarantined:,} row(s) quarantined, 0 after"
        )
        for name, rows in report.loaded.items():
            if rows:
                print(f"         {name:14s} {rows:>10,}")

        if "publish" in stages:
            published = publish(report.tables, returns, url=args.warehouse_url, schema=args.schema)
            print("publish  report tables")
            for name, rows in published.items():
                print(f"         {name:28s} {rows:>6,}")

    print(f"\nDone in {time.time() - started:.0f}s.")


if __name__ == "__main__":
    main()
