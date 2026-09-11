"""Evaluate RootSignal's forecasting models and report measured accuracy.

Runs a rolling-origin backtest over the cleaned daily series and prints one
accuracy table per metric. Every model is refitted at each origin on data
strictly before the window it is scored on, so the numbers here are
out-of-sample.

The script is the source of the accuracy figures quoted in docs/forecasting.md.
Re-run it after any change to the generator, the cleaning layer, or the models
rather than copying numbers forward by hand.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from rootsignal.dataset import load_tables_for_analysis
from rootsignal.forecasting import (
    build_daily_series,
    compare_against_baseline,
    extract_metric,
    rolling_origin_evaluate,
    summarise_backtest,
)

REPORT_COLUMNS = [
    "model",
    "folds",
    "n_observations",
    "mae",
    "rmse",
    "wape",
    "mape",
    "bias",
    "wape_improvement_vs_baseline",
]


def evaluate_metric(
    series: pd.Series,
    horizon: int,
    initial_train: int,
    step: int,
    baseline: str,
) -> pd.DataFrame:
    predictions = rolling_origin_evaluate(
        series, horizon=horizon, initial_train=initial_train, step=step
    )
    summary = summarise_backtest(predictions)
    return compare_against_baseline(summary, baseline=baseline)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest RootSignal forecasting models.")
    parser.add_argument("--input-dir", default="data/raw/generated", help="Directory of raw CSV tables.")
    parser.add_argument("--horizon", type=int, default=7, help="Forecast horizon in days.")
    parser.add_argument("--initial-train", type=int, default=28, help="Days before the first origin.")
    parser.add_argument("--step", type=int, default=7, help="Days between successive origins.")
    parser.add_argument("--baseline", default="naive", help="Model to measure improvement against.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for CSV output.")
    args = parser.parse_args()

    tables = load_tables_for_analysis(args.input_dir)
    daily = build_daily_series(tables["fact_sales"], tables["fact_orders"])

    print(
        f"Daily series: {len(daily)} days, "
        f"{daily.index.min().date()} to {daily.index.max().date()}\n"
        f"Backtest: horizon={args.horizon}, initial_train={args.initial_train}, step={args.step}\n"
    )

    reports = {}
    for metric in daily.columns:
        series = extract_metric(daily, metric)
        summary = evaluate_metric(series, args.horizon, args.initial_train, args.step, args.baseline)
        reports[metric] = summary

        spread = float(series.max() - series.min())
        print(f"===== {metric} =====")
        if spread == 0.0 or series.std() < 0.01 * max(abs(series.mean()), 1.0):
            print(
                "  NOTE: this series is near-constant in the sample data, so every model\n"
                "  scores near-zero error. The comparison is not informative here."
            )
        print(summary[REPORT_COLUMNS].round(4).to_string(index=False))
        print()

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for metric, summary in reports.items():
            summary[REPORT_COLUMNS].to_csv(output_dir / f"forecast_accuracy_{metric}.csv", index=False)
        print(f"Wrote {len(reports)} accuracy reports to {output_dir}")


if __name__ == "__main__":
    main()
