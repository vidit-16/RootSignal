"""Write up the current signals as a readable briefing.

Works with no API key: the briefing is composed from the evidence package by
deterministic code. If OPENAI_API_KEY is set and --rewrite is passed, a language
model rephrases it, and the rewrite is checked against the same evidence before
being used. A rewrite containing a figure that cannot be traced back, or
claiming a cause, is discarded.
"""

from __future__ import annotations

import argparse

from rootsignal.console import use_utf8_output
from rootsignal.dataset import load_tables_for_analysis
from rootsignal.explanation import explain, is_available, write_briefing, write_summary
from rootsignal.signals import detect_signals


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="Explain RootSignal findings in plain English.")
    parser.add_argument("--input-dir", default="data/raw/generated")
    parser.add_argument("--metric", default="fill_rate")
    parser.add_argument("--dimension", default="region_code,category")
    parser.add_argument("--period", default="week", choices=["day", "week", "month"])
    parser.add_argument("--current-period", default=None)
    parser.add_argument("--comparison-period", default=None)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--min-confidence", default=None, choices=["low", "medium", "high"])
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="Rephrase with a language model, if one is configured. Off by default.",
    )
    parser.add_argument("--model", default=None, help="Model name for the rewrite.")
    parser.add_argument("--base-url", default=None, help="Any OpenAI-compatible endpoint.")
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
        min_confidence=args.min_confidence,
    )
    packages = [signal.as_dict() for signal in signals]

    print(write_summary(packages, tables))
    print("\n" + "=" * 78 + "\n")

    for position, package in enumerate(packages, start=1):
        print(f"--- {position} ---\n")
        if args.rewrite:
            options = {"model": args.model} if args.model else {}
            result = explain(package, tables, base_url=args.base_url, **options)
            print(result.text)
            print(f"\n[{result.source}] {result.note}")
        else:
            print(write_briefing(package, tables).as_text())
        print()

    if not args.rewrite:
        state = "configured" if is_available() else "not configured"
        print(
            f"Written without a language model. A model is {state}; "
            "pass --rewrite to rephrase, which changes wording only."
        )


if __name__ == "__main__":
    main()
