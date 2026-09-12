"""Measure whether the signal engine tells the planted scenarios apart.

The sample dataset carries four deliberately different situations: a supply
constraint, a demand decline, a mix shift, and a region where nothing happens.
This script runs the engine over each and reports what it concluded, sweeping
the confidence floor so the trade-off between finding things and staying quiet
is visible rather than hidden behind one chosen threshold.

Results are reported as measured, including misses.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from rootsignal.console import use_utf8_output
from rootsignal.dataset import load_tables_for_analysis
from rootsignal.signals import evaluate_scenarios, load_scenarios, summarise_evaluation

DETAIL_COLUMNS = [
    "confidence_floor",
    "scenario",
    "metric",
    "outcome",
    "matched_segment",
    "matched_pattern",
    "rank",
    "signals_returned",
]


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Evaluate RootSignal scenario detection.")
    parser.add_argument("--input-dir", default="data/raw/generated", help="Directory of raw CSV tables.")
    parser.add_argument("--floors", default="high,medium", help="Comma-separated confidence floors.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for CSV output.")
    args = parser.parse_args()

    tables = load_tables_for_analysis(args.input_dir)
    scenarios = load_scenarios(args.input_dir)
    floors = tuple(part.strip() for part in args.floors.split(",") if part.strip())

    results = evaluate_scenarios(tables, scenarios, confidence_floors=floors)
    summary = summarise_evaluation(results)

    pd.set_option("display.width", 240)
    print("Planted scenarios\n")
    for scenario in scenarios:
        expected = scenario["expected_pattern"] or "nothing planted"
        print(f"  {scenario['name']:24s} {scenario['region_code']:4s} expect={expected}")
        print(f"      {scenario['description']}")
    print("\nPer-scenario outcome\n")
    print(results[DETAIL_COLUMNS].to_string(index=False))
    print("\nSummary\n")
    print(summary.to_string(index=False))

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results.to_csv(output_dir / "scenario_results.csv", index=False)
        summary.to_csv(output_dir / "scenario_summary.csv", index=False)
        print(f"\nWrote scenario evaluation to {output_dir}")


if __name__ == "__main__":
    main()
