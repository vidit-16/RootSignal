"""Build the operational Excel reports from the cleaned dataset.

Every figure in these workbooks is produced by the tested analytical layers.
Excel is where this system reports, not where it computes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rootsignal.cleaning import clean_dataset
from rootsignal.ingestion import load_dataset
from rootsignal.reporting import available_reports, build_all_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RootSignal Excel reports.")
    parser.add_argument("--input-dir", default="data/raw/generated", help="Directory of raw CSV tables.")
    parser.add_argument("--output-dir", default="reports", help="Where to write the workbooks.")
    parser.add_argument("--reports", default=None, help="Comma-separated subset; omit for all.")
    parser.add_argument("--current-period", default=None, help="Signal report period, e.g. 2026-02-23.")
    parser.add_argument("--comparison-period", default=None, help="Signal report comparison period.")
    parser.add_argument("--list", action="store_true", help="List available reports and exit.")
    args = parser.parse_args()

    if args.list:
        print("Available reports:")
        for name in available_reports():
            print(f"  {name}")
        return

    names = [part.strip() for part in args.reports.split(",")] if args.reports else None
    tables = clean_dataset(load_dataset(Path(args.input_dir))).tables
    written = build_all_reports(
        tables,
        output_dir=args.output_dir,
        names=names,
        current_period=args.current_period,
        comparison_period=args.comparison_period,
    )

    print(f"Wrote {len(written)} workbook(s) to {args.output_dir}\n")
    for path in written:
        print(f"  {path.name:32s} {path.stat().st_size / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
