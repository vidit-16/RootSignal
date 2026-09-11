"""Run RootSignal over a real public dataset instead of the generated one.

Downloads UCI Online Retail II, maps it onto the business model, and runs every
analysis the data can actually support. Analyses that need facts this dataset
does not contain are skipped and reported as skipped, with the reason.

Nothing in the analytical layers changes for this. Only the adapter exists.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from rootsignal.adapters.online_retail import adapt, returns_frame
from rootsignal.analysis import calculate_trend, summarise_by_period
from rootsignal.cleaning import clean_dataset
from rootsignal.decomposition import decompose_additive, rank_drivers, total_movement
from rootsignal.forecasting import (
    build_daily_series,
    compare_against_baseline,
    extract_metric,
    rolling_origin_evaluate,
    summarise_backtest,
)
from rootsignal.validation import DatasetValidator


def heading(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RootSignal on a real public dataset.")
    parser.add_argument("--cache-dir", default="data/raw/external/online_retail")
    parser.add_argument("--sheets", type=int, default=None, help="Limit sheets read, for speed.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for CSV output.")
    args = parser.parse_args()

    pd.set_option("display.width", 220)
    started = time.time()

    heading("The dataset")
    dataset = adapt(cache_dir=args.cache_dir, sheets=args.sheets)
    print(dataset.capabilities.describe())

    heading("Validation, on the data as published")
    raw_report = DatasetValidator().validate(dataset.tables)
    print(f"{len(raw_report.errors())} error(s), {len(raw_report.issues) - len(raw_report.errors())} warning(s)")
    for issue in raw_report.issues:
        print(f"  [{issue.severity}] {issue.table}.{issue.check}: {issue.rows:,} row(s) — {issue.message}")

    heading("Cleaning")
    result = clean_dataset(dataset.tables)
    print(result.audit.to_string(index=False) if not result.audit.empty else "No changes were needed.")
    quarantined = sum(len(frame) for frame in result.quarantined.values())
    print(f"\n{quarantined:,} row(s) quarantined.")

    clean_report = DatasetValidator().validate(result.tables)
    print(f"After cleaning: {len(clean_report.errors())} error(s).")
    for issue in clean_report.errors():
        print(f"  [{issue.severity}] {issue.table}.{issue.check}: {issue.message}")

    tables = result.tables
    sales = tables["fact_sales"]
    print(f"\n{len(sales):,} sales lines, {sales['order_id'].nunique():,} invoices, "
          f"{sales['sku_id'].nunique():,} products, {sales['region_code'].nunique()} countries.")
    print(f"Revenue: {sales['net_sales'].sum():,.0f}   "
          f"Dates: {sales['date'].min()} to {sales['date'].max()}")

    heading("Returns, reported as returns")
    returns = returns_frame(cache_dir=args.cache_dir, sheets=args.sheets)
    return_rate = returns["returned_units"].sum() / max(sales["units"].sum(), 1)
    print(f"{len(returns):,} returned lines, {returns['returned_units'].sum():,.0f} units.")
    print(f"Returned units as a share of units sold: {return_rate:.2%}")
    print("This is a returns rate. It is not a fill rate, and the two must not be read as the same thing.")

    heading("Monthly trade")
    monthly = summarise_by_period(tables, period="month")
    # sales_order_count is the invoice count derived from sales. order_count
    # comes from the order fact, which is empty here, so showing it would print a
    # column of zeros that looks like a finding.
    print(
        monthly[["period_start", "net_sales", "sales_units", "sales_order_count", "aov"]]
        .to_string(index=False)
    )

    heading("Month-over-month movement")
    trend = calculate_trend(monthly, "net_sales")
    print(trend[["period_start", "previous_value", "value", "absolute_change", "pct_change"]].to_string(index=False))

    heading("Which countries moved the business")
    by_region = summarise_by_period(tables, period="month", group_by=["region_code"])
    decomposition = decompose_additive(by_region, "net_sales", ["region_code"])
    print(f"Latest month moved {total_movement(decomposition):,.0f}\n")
    print(
        rank_drivers(decomposition, "absolute", 8)[
            ["rank", "segment", "contribution", "share_of_absolute_movement"]
        ].to_string(index=False)
    )

    heading("Forecast accuracy on daily revenue")
    # Only the metrics this dataset supports. Asking for an orders series would
    # be refused, correctly, because there is no order fact behind it.
    daily = build_daily_series(sales, metrics=["net_sales", "units"])
    series = extract_metric(daily, "net_sales")
    print(f"{len(series)} days of history.")
    accuracy = compare_against_baseline(
        summarise_backtest(rolling_origin_evaluate(series, horizon=7, initial_train=56, step=14))
    )
    print(accuracy[["model", "folds", "n_observations", "mae", "wape", "wape_improvement_vs_baseline"]].to_string(index=False))

    heading("Skipped, and why")
    for analysis in dataset.capabilities.unsupported_analyses:
        print(f"  - {dataset.capabilities.why_unsupported(analysis)}")

    if args.output_dir:
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        monthly.to_csv(output / "external_monthly.csv", index=False)
        accuracy.to_csv(output / "external_forecast_accuracy.csv", index=False)
        decomposition.to_csv(output / "external_country_contribution.csv", index=False)
        print(f"\nWrote results to {output}")

    print(f"\nCompleted in {time.time() - started:.1f}s.")


if __name__ == "__main__":
    main()
