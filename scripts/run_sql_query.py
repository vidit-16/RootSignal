"""Run one of the analytical SQL queries against the cleaned dataset.

Builds a database from the cleaned tables, registers the staging and mart
views, and executes a named query from sql/analytics.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from rootsignal.cleaning import clean_dataset
from rootsignal.ingestion import load_dataset
from rootsignal.sql import available_queries, build_database, run_query_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a RootSignal analytical SQL query.")
    parser.add_argument("query", nargs="?", help="Query name; omit to list what is available.")
    parser.add_argument("--input-dir", default="data/raw/generated", help="Directory of raw CSV tables.")
    parser.add_argument("--limit", type=int, default=25, help="Rows to print (0 for all).")
    parser.add_argument("--output-dir", default=None, help="Optional directory for CSV output.")
    args = parser.parse_args()

    names = available_queries()
    if not args.query:
        print("Available queries:")
        for name in names:
            print(f"  {name}")
        return
    if args.query not in names:
        raise SystemExit(f"Unknown query '{args.query}'. Available: {names}")

    tables = clean_dataset(load_dataset(Path(args.input_dir))).tables
    conn = build_database(tables)
    result = run_query_file(conn, args.query)

    pd.set_option("display.width", 220)
    shown = result if args.limit == 0 else result.head(args.limit)
    print(f"{args.query}: {len(result)} rows\n")
    print(shown.to_string(index=False))

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{args.query}.csv"
        result.to_csv(destination, index=False)
        print(f"\nWrote {destination}")


if __name__ == "__main__":
    main()
