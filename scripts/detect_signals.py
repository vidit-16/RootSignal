"""Run the RootSignal engine and print the signals worth investigating.

Loads the raw dataset, cleans it, and assembles ranked evidence-backed signals
for a metric movement. Every figure printed is computed deterministically; the
prose is assembled from those figures by fixed templates.

This script produces the output quoted in docs/signals.md. Re-run it after any
change to the generator or the analytical layers rather than copying numbers
forward by hand.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rootsignal.console import use_utf8_output
from rootsignal.dataset import load_tables_for_analysis
from rootsignal.signals import detect_signals, explain_signal, signals_to_frame


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Detect RootSignals in a business dataset.")
    parser.add_argument("--input-dir", default="data/raw/generated", help="Directory of raw CSV tables.")
    parser.add_argument("--metric", default="fill_rate", help="Metric whose movement to investigate.")
    parser.add_argument(
        "--dimension",
        default="region_code,category",
        help="Comma-separated dimensions to attribute the movement across.",
    )
    parser.add_argument("--period", default="week", choices=["day", "week", "month"])
    parser.add_argument("--current-period", default=None, help="Period to analyse, e.g. 2026-02-16.")
    parser.add_argument("--comparison-period", default=None, help="Period to compare against.")
    parser.add_argument("--top-n", type=int, default=3, help="How many segments to report.")
    parser.add_argument("--json", action="store_true", help="Emit the full evidence packages as JSON.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for a CSV summary.")
    args = parser.parse_args()

    tables = load_tables_for_analysis(args.input_dir)
    signals = detect_signals(
        tables,
        metric=args.metric,
        dimension=[part.strip() for part in args.dimension.split(",") if part.strip()],
        period=args.period,
        current_period=args.current_period,
        comparison_period=args.comparison_period,
        top_n=args.top_n,
    )

    if not signals:
        print("No signals detected for the requested movement.")
        return

    if args.json:
        print(json.dumps([signal.as_dict() for signal in signals], indent=2, default=str))
        return

    print(f"{len(signals)} signal(s), highest priority first\n")
    print(signals_to_frame(signals).to_string(index=False))
    print()

    for position, signal in enumerate(signals, start=1):
        print(f"--- {position}. {signal.segment} ---")
        print(explain_signal(signal))
        print("\n    Confidence criteria:")
        for criterion in signal.confidence.criteria:
            print(f"      [{'x' if criterion.met else ' '}] {criterion.name}: {criterion.detail}")
        if signal.impact is not None:
            print("\n    Impact assumptions:")
            for assumption in signal.impact.assumptions:
                print(f"      - {assumption}")
        for note in signal.notes:
            print(f"\n    Note: {note}")
        print()

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"root_signals_{args.metric}.csv"
        signals_to_frame(signals).to_csv(destination, index=False)
        print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
