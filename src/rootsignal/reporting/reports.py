"""Build the operational Excel reports from already-computed analytics.

Each report composes layers that are tested elsewhere: the KPI engine, the
period summaries, the forecast backtest, the signal engine. None of them
recomputes a metric. Excel is where this system reports, not where it thinks.

The caveats attached to each workbook are part of the deliverable. An impact
estimate that arrives without its basis will be read as a measured loss, and a
forecast accuracy figure without its window will be read as a guarantee.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from ..analysis import (
    calculate_trend,
    summarise_by_period,
    variance_vs_target,
)
from ..forecasting import (
    build_daily_series,
    compare_against_baseline,
    extract_metric,
    rolling_origin_evaluate,
    summarise_backtest,
)
from ..explanation import write_briefing, write_summary
from ..impact.estimation import BASELINE_ASSUMPTIONS
from ..metrics import calculate_primary_secondary_mix
from ..signals import detect_signals, signals_to_frame
from .workbook import new_workbook, save, write_cover, write_table

DEFAULT_OUTPUT_DIR = Path("reports")

SHARED_NOTES = (
    "Figures are produced from the cleaned dataset. Rows quarantined during "
    "cleaning are excluded and are listed in the cleaning audit.",
    "Weekly figures cover Monday to Sunday. Where a week is partial it is "
    "either excluded or its day count is shown; a short week compared against a "
    "full one is not a like-for-like comparison.",
)


def _daily_sales_tracker(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    daily = summarise_by_period(tables, period="day")
    trend = calculate_trend(daily, "net_sales")

    tracker = daily[
        [
            "period_start",
            "net_sales",
            "sales_units",
            "order_count",
            "ordered_units",
            "fulfilled_units",
            "cancelled_units",
            "fill_rate",
            "aov",
        ]
    ].rename(columns={"period_start": "date"})

    movement = trend[["period_start", "previous_value", "absolute_change", "pct_change"]].rename(
        columns={
            "period_start": "date",
            "previous_value": "prior_day_net_sales",
            "absolute_change": "day_over_day_change",
            "pct_change": "day_over_day_pct",
        }
    )
    tracker = tracker.merge(movement, on="date", how="left", validate="one_to_one")

    targets = variance_vs_target(daily, tables["fact_targets"], "net_sales", "sales_target")
    plan = targets[["period_start", "comparison_value", "variance", "variance_pct"]].rename(
        columns={
            "period_start": "date",
            "comparison_value": "sales_target",
            "variance": "target_variance",
            "variance_pct": "target_variance_pct",
        }
    )
    tracker = tracker.merge(plan, on="date", how="left", validate="one_to_one")
    return {"Daily tracker": tracker}


def _sales_performance(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    sheets: dict[str, pd.DataFrame] = {}
    for label, dimension in (
        ("By region", ["region_code"]),
        ("By category", ["category"]),
        ("By channel", ["channel"]),
        ("By KAM", ["kam_id"]),
    ):
        weekly = summarise_by_period(tables, period="week", group_by=dimension)
        columns = [
            "period_start",
            *dimension,
            "net_sales",
            "sales_units",
            "order_count",
            "fill_rate",
            "aov",
            "discount_value",
        ]
        sheets[label] = weekly[columns].rename(columns={"period_start": "week_start"})

    plan = summarise_by_period(tables, period="week", group_by=["region_code", "category", "channel"])
    variance = variance_vs_target(
        plan,
        tables["fact_targets"],
        "net_sales",
        "sales_target",
        period="week",
        group_by=["region_code", "category", "channel"],
    )
    sheets["Versus plan"] = variance[
        [
            "period_start",
            "region_code",
            "category",
            "channel",
            "actual",
            "comparison_value",
            "variance",
            "variance_pct",
        ]
    ].rename(
        columns={
            "period_start": "week_start",
            "actual": "net_sales",
            "comparison_value": "sales_target",
        }
    )
    return sheets


def _fill_rate_report(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    weekly = summarise_by_period(tables, period="week", group_by=["region_code", "category"])
    service = variance_vs_target(
        weekly,
        tables["fact_targets"],
        "fill_rate",
        "fill_rate_target",
        period="week",
        group_by=["region_code", "category"],
    )
    service = service[
        ["period_start", "region_code", "category", "actual", "comparison_value", "variance"]
    ].rename(
        columns={
            "period_start": "week_start",
            "actual": "fill_rate",
            "comparison_value": "fill_rate_target",
            "variance": "fill_rate_variance",
        }
    )

    detail = weekly[
        [
            "period_start",
            "region_code",
            "category",
            "ordered_units",
            "fulfilled_units",
            "cancelled_units",
            "fill_rate",
            "cancellation_rate",
        ]
    ].copy()
    detail["unfulfilled_units"] = detail["ordered_units"] - detail["fulfilled_units"]
    detail = detail.rename(columns={"period_start": "week_start"})

    movement = calculate_trend(weekly, "fill_rate", group_by=["region_code", "category"])[
        ["period_start", "region_code", "category", "previous_value", "value", "absolute_change"]
    ].rename(
        columns={
            "period_start": "week_start",
            "previous_value": "prior_fill_rate",
            "value": "fill_rate",
            "absolute_change": "fill_rate_change",
        }
    )

    return {
        "Versus target": service,
        "Service detail": detail,
        "Week over week": movement.sort_values("fill_rate_change"),
    }


def _primary_secondary(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    sales = tables["fact_sales"]
    sheets = {}
    for label, groups in (
        ("By region", ["region_code"]),
        ("By channel", ["channel"]),
        ("By category", ["category"]),
    ):
        sheets[label] = calculate_primary_secondary_mix(sales, groups)
    return sheets


def _forecast_report(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    daily = build_daily_series(tables["fact_sales"], tables["fact_orders"])
    sheets: dict[str, pd.DataFrame] = {}

    for metric in daily.columns:
        series = extract_metric(daily, metric)
        predictions = rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7)
        summary = compare_against_baseline(summarise_backtest(predictions))
        sheets[f"Accuracy {metric}"[:31]] = summary

        best = summary.iloc[0]["model"]
        actual_vs_forecast = predictions[predictions["model"] == best][
            ["fold", "origin_date", "date", "actual", "forecast"]
        ].copy()
        actual_vs_forecast["error"] = (
            actual_vs_forecast["forecast"] - actual_vs_forecast["actual"]
        ).round(4)
        sheets[f"Backtest {metric}"[:31]] = actual_vs_forecast

    sheets["Daily series"] = daily.reset_index().rename(columns={"index": "date"})
    return sheets


def _root_signal_report(
    tables: dict[str, pd.DataFrame],
    current_period: str | None,
    comparison_period: str | None,
) -> dict[str, pd.DataFrame]:
    signals = detect_signals(
        tables,
        metric="fill_rate",
        dimension=["region_code", "category"],
        period="week",
        current_period=current_period,
        comparison_period=comparison_period,
        top_n=5,
    )
    sheets = {"Signals": signals_to_frame(signals)}

    # A written brief travels better than a table of figures. Each signal is
    # composed here by deterministic code, from the same evidence the table
    # shows, so the prose and the numbers cannot drift apart.
    packages = [signal.as_dict() for signal in signals]
    briefing_rows = [{"section": "Summary", "segment": "", "text": write_summary(packages, tables)}]
    for signal, entry in zip(signals, packages):
        briefing = write_briefing(entry, tables)
        for section, text in briefing.as_dict().items():
            if section == "caveats" or not text:
                continue
            briefing_rows.append(
                {"section": section.replace("_", " ").title(), "segment": signal.segment, "text": text}
            )
    sheets["Briefing"] = pd.DataFrame(briefing_rows)

    evidence_rows = []
    criteria_rows = []
    for signal in signals:
        for name, observation in signal.supporting_evidence.items():
            evidence_rows.append(
                {
                    "segment": signal.segment,
                    "observation": name,
                    "before": observation["before"],
                    "after": observation["after"],
                    "change": observation["change"],
                    "change_pct": observation["change_pct"],
                    "direction": observation["direction"],
                }
            )
        for criterion in signal.confidence.criteria:
            criteria_rows.append(
                {
                    "segment": signal.segment,
                    "criterion": criterion.name,
                    "met": criterion.met,
                    "detail": criterion.detail,
                }
            )

    sheets["Evidence"] = pd.DataFrame(evidence_rows)
    sheets["Confidence criteria"] = pd.DataFrame(criteria_rows)

    impact_rows = [
        {
            "segment": signal.segment,
            "estimated_impact": signal.impact.value,
            "basis": signal.impact.basis,
            **signal.impact.components,
        }
        for signal in signals
        if signal.impact is not None
    ]
    sheets["Impact"] = pd.DataFrame(impact_rows)
    return sheets


REPORTS = {
    "daily_sales_tracker": {
        "title": "Daily sales tracker",
        "description": (
            "One row per trading day: sales, units, orders, fulfilment and the "
            "comparison against both the prior day and the plan."
        ),
        "notes": (
            "Order counts are distinct counts. An order carrying several SKUs is "
            "one order, and these figures must not be summed across a breakdown "
            "that splits orders by product.",
        ),
        "build": lambda tables, **_: _daily_sales_tracker(tables),
    },
    "sales_performance": {
        "title": "Sales performance",
        "description": (
            "Weekly commercial performance by region, category, channel and key "
            "account manager, with plan-versus-actual variance."
        ),
        "notes": (
            "Each breakdown is a complete view of the same trade from a different "
            "angle. Figures from different sheets describe the same sales and must "
            "not be added together.",
        ),
        "build": lambda tables, **_: _sales_performance(tables),
    },
    "fill_rate_report": {
        "title": "Fill rate and service level",
        "description": (
            "Weekly fulfilment performance by region and category against the "
            "service-level target, with the week-over-week movement."
        ),
        "notes": (
            "Fill rate is rebuilt from summed units at each level. Averaging the "
            "fill rates of several weeks or segments gives a different and wrong "
            "answer, because it weights a quiet period the same as a busy one.",
            "A period with no demand has no fill rate, and is left blank rather "
            "than shown as zero.",
        ),
        "build": lambda tables, **_: _fill_rate_report(tables),
    },
    "primary_secondary_report": {
        "title": "Primary and secondary sales",
        "description": (
            "Sell-in against sell-through by region, channel and category, with "
            "the mix between them."
        ),
        "notes": (
            "Primary and secondary are different commercial events and are not "
            "additive as a measure of end demand.",
        ),
        "build": lambda tables, **_: _primary_secondary(tables),
    },
    "forecast_report": {
        "title": "Forecast accuracy",
        "description": (
            "Model comparison from a rolling-origin backtest, and the actual "
            "against forecast values behind it."
        ),
        "notes": (
            "Accuracy is measured out-of-sample: each model is refitted at every "
            "origin on data strictly before the window it is scored on.",
            "These figures describe accuracy on a 60-day history at a daily "
            "company-wide grain. They are not a guarantee of future accuracy, and "
            "segment-level forecasts have not been evaluated.",
            "MAPE is left blank where a near-zero actual would make it "
            "meaningless. WAPE is the metric to read.",
        ),
        "build": lambda tables, **_: _forecast_report(tables),
    },
    "root_signal_report": {
        "title": "RootSignal investigation report",
        "description": (
            "Movements worth investigating, with the operational evidence around "
            "them, an estimate of what they are worth, and how far the evidence "
            "supports the reading."
        ),
        "notes": (
            "Each signal states what its evidence is CONSISTENT WITH. None of "
            "these is a proven cause, and the alternative explanation on each row "
            "is there to be weighed rather than ignored.",
            "Confidence is a count of the named criteria on the Confidence "
            "criteria sheet. It is not a probability and none of the criteria is a "
            "statistical test.",
            *BASELINE_ASSUMPTIONS,
        ),
        "build": lambda tables, current_period=None, comparison_period=None: _root_signal_report(
            tables, current_period, comparison_period
        ),
    },
}


def available_reports() -> list[str]:
    """Names of the reports that can be built."""
    return sorted(REPORTS)


def build_report(
    name: str,
    tables: dict[str, pd.DataFrame],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    **options,
) -> Path:
    """Build one workbook and return where it was written."""
    if name not in REPORTS:
        raise ValueError(f"Unknown report '{name}'; available: {available_reports()}")

    definition = REPORTS[name]
    sheets = definition["build"](tables, **options)

    workbook = new_workbook()
    for sheet_name, frame in sheets.items():
        write_table(workbook, sheet_name, frame)
    write_cover(
        workbook,
        definition["title"],
        definition["description"],
        notes=tuple(definition["notes"]) + SHARED_NOTES,
    )
    return save(workbook, Path(output_dir) / f"{name}.xlsx")


def build_all_reports(
    tables: dict[str, pd.DataFrame],
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    names: Sequence[str] | None = None,
    **options,
) -> list[Path]:
    """Build every report, or a named subset."""
    selected = list(names or available_reports())
    return [build_report(name, tables, output_dir, **options) for name in selected]
