"""Prepare everything the dashboard displays, without importing Streamlit.

The dashboard is a rendering layer. Keeping its data preparation here — in
ordinary functions returning ordinary frames — means every figure it shows can
be tested without starting a web server, and means the pages contain layout
rather than analysis.

Nothing in this module computes a metric. Each view composes layers that are
tested elsewhere: the period summaries, variance analysis, the forecast
backtest, the signal engine. A dashboard that re-derived fill rate from whatever
columns were to hand would become a second definition of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from ..analysis import (
    PERIOD_COLUMN,
    calculate_trend,
    summarise_by_period,
    variance_vs_target,
)
from ..cleaning import clean_dataset
from ..dataset import check_contracts
from ..forecasting import (
    build_daily_series,
    compare_against_baseline,
    extract_metric,
    rolling_origin_evaluate,
    summarise_backtest,
)
from ..ingestion import load_dataset
from ..metrics import calculate_primary_secondary_mix
from ..signals import detect_signals, signals_to_frame, summarise_inventory_by_period

DEFAULT_INPUT_DIR = "data/raw/generated"


def load_business_data(input_dir: str | Path = DEFAULT_INPUT_DIR) -> dict:
    """Load and clean the dataset once, keeping the audit trail alongside it.

    The cleaning audit is returned with the data rather than discarded, so the
    dashboard can show what was changed on the way in instead of presenting
    cleaned figures as though they arrived that way.
    """
    raw = load_dataset(Path(input_dir))
    result = clean_dataset(raw)
    check_contracts(result.tables, source=str(Path(input_dir)))
    return {
        "tables": result.tables,
        "audit": result.audit,
        "quarantined": result.quarantined,
        "raw_row_counts": {name: len(frame) for name, frame in raw.items()},
    }


def _latest_two_periods(summary: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    periods = sorted(pd.to_datetime(summary[PERIOD_COLUMN]).unique())
    if len(periods) < 2:
        return None
    return periods[-1], periods[-2]


def overview(tables: dict[str, pd.DataFrame], period: str = "week") -> dict:
    """Headline figures for the most recent complete period, and how they moved.

    Partial periods are excluded, so the headline is never a short week dressed
    up as a full one.
    """
    summary = summarise_by_period(tables, period=period)
    if summary.empty:
        return {"available": False}

    bounds = _latest_two_periods(summary)
    if bounds is None:
        return {"available": False}
    current, previous = bounds

    latest = summary.loc[pd.to_datetime(summary[PERIOD_COLUMN]) == current].iloc[0]
    prior = summary.loc[pd.to_datetime(summary[PERIOD_COLUMN]) == previous].iloc[0]

    headline = {}
    for metric in ("net_sales", "sales_units", "order_count", "fill_rate", "aov"):
        now, before = float(latest[metric]), float(prior[metric])
        headline[metric] = {
            "value": now,
            "previous": before,
            "change": now - before,
            "change_pct": (now - before) / abs(before) if before else None,
        }

    plan = variance_vs_target(summary, tables["fact_targets"], "net_sales", "sales_target", period=period)
    plan_row = plan.loc[pd.to_datetime(plan[PERIOD_COLUMN]) == current]
    headline["target"] = (
        {
            "actual": float(plan_row.iloc[0]["actual"]),
            "target": float(plan_row.iloc[0]["comparison_value"]),
            "variance": float(plan_row.iloc[0]["variance"]),
            "variance_pct": plan_row.iloc[0]["variance_pct"],
        }
        if len(plan_row)
        else None
    )

    return {
        "available": True,
        "period": current,
        "comparison_period": previous,
        "period_type": period,
        "headline": headline,
        "trend": calculate_trend(summary, "net_sales"),
        "summary": summary,
    }


def sales_views(tables: dict[str, pd.DataFrame], period: str = "week") -> dict[str, pd.DataFrame]:
    """Commercial performance cut the ways a sales team asks for it."""
    views = {
        "daily": summarise_by_period(tables, period="day"),
        "by_region": summarise_by_period(tables, period=period, group_by=["region_code"]),
        "by_category": summarise_by_period(tables, period=period, group_by=["category"]),
        "by_channel": summarise_by_period(tables, period=period, group_by=["channel"]),
        "by_kam": summarise_by_period(tables, period=period, group_by=["kam_id"]),
        "mix": calculate_primary_secondary_mix(tables["fact_sales"], ["region_code"]),
    }

    plan = summarise_by_period(tables, period=period, group_by=["region_code", "category"])
    views["versus_plan"] = variance_vs_target(
        plan,
        tables["fact_targets"],
        "net_sales",
        "sales_target",
        period=period,
        group_by=["region_code", "category"],
    )
    return views


def supply_views(tables: dict[str, pd.DataFrame], period: str = "week") -> dict[str, pd.DataFrame]:
    """Fulfilment and stock, at the grains inventory actually supports.

    Inventory is held by SKU, warehouse and region, so it can corroborate a
    regional or category view but has nothing to say about a channel or a
    customer.
    """
    segments = summarise_by_period(tables, period=period, group_by=["region_code", "category"])
    service = variance_vs_target(
        segments,
        tables["fact_targets"],
        "fill_rate",
        "fill_rate_target",
        period=period,
        group_by=["region_code", "category"],
    )

    by_sku = summarise_by_period(tables, period=period, group_by=["sku_id"])
    by_sku = by_sku.merge(
        tables["dim_sku"][["sku_id", "sku_name", "category"]], on="sku_id", validate="many_to_one"
    )
    by_sku["unfulfilled_units"] = by_sku["ordered_units"] - by_sku["fulfilled_units"]

    return {
        "segments": segments,
        "service": service,
        # Region-level series are built at region grain rather than by collapsing
        # the region-by-category frame. A fill rate must be rebuilt from summed
        # units at whatever level it is shown, and plotting several category rows
        # per region as one line would draw a zigzag rather than a trend.
        "by_region": summarise_by_period(tables, period=period, group_by=["region_code"]),
        "inventory": _match_periods(
            summarise_inventory_by_period(tables, period=period, group_by=["region_code", "category"]),
            segments,
        ),
        "inventory_by_region": _match_periods(
            summarise_inventory_by_period(tables, period=period, group_by=["region_code"]),
            segments,
        ),
        "by_sku": by_sku,
        "fill_rate_trend": calculate_trend(segments, "fill_rate", group_by=["region_code", "category"]),
    }


def _match_periods(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Restrict a frame to the periods the reference covers.

    Inventory summaries do not drop partial periods, so without this the stock
    charts would carry a final short week that the commercial charts beside them
    exclude, and the pair would appear to disagree.
    """
    periods = set(pd.to_datetime(reference[PERIOD_COLUMN]))
    return frame.loc[pd.to_datetime(frame[PERIOD_COLUMN]).isin(periods)].reset_index(drop=True)


def forecast_views(
    tables: dict[str, pd.DataFrame],
    horizon: int = 7,
    initial_train: int = 28,
    step: int = 7,
) -> dict:
    """Model comparison and the actuals behind it, per forecastable metric.

    Accuracy is out-of-sample: every model is refitted at each origin on data
    strictly before the window it is scored on.
    """
    daily = build_daily_series(tables["fact_sales"], tables["fact_orders"])
    accuracy: dict[str, pd.DataFrame] = {}
    backtests: dict[str, pd.DataFrame] = {}

    for metric in daily.columns:
        series = extract_metric(daily, metric)
        predictions = rolling_origin_evaluate(
            series, horizon=horizon, initial_train=initial_train, step=step
        )
        summary = compare_against_baseline(summarise_backtest(predictions))
        accuracy[metric] = summary

        best = summary.iloc[0]["model"]
        frame = predictions[predictions["model"] == best].copy()
        frame["error"] = frame["forecast"] - frame["actual"]
        backtests[metric] = frame

    return {
        "series": daily,
        "accuracy": accuracy,
        "backtests": backtests,
        "settings": {"horizon": horizon, "initial_train": initial_train, "step": step},
    }


def signal_views(
    tables: dict[str, pd.DataFrame],
    metric: str = "fill_rate",
    dimension: Sequence[str] = ("region_code", "category"),
    period: str = "week",
    current_period: str | None = None,
    comparison_period: str | None = None,
    top_n: int = 5,
    min_confidence: str | None = None,
) -> dict:
    """Signals with the evidence, criteria and impact behind each one.

    The reasoning is returned alongside the conclusions rather than separately,
    so a page cannot show a confidence level without the criteria that produced
    it.
    """
    signals = detect_signals(
        tables,
        metric=metric,
        dimension=list(dimension),
        period=period,
        current_period=current_period,
        comparison_period=comparison_period,
        top_n=top_n,
        min_confidence=min_confidence,
    )

    evidence_rows, criteria_rows = [], []
    for signal in signals:
        for name, observation in signal.supporting_evidence.items():
            # The observation already carries its own name; keep one copy of it.
            fields = {key: value for key, value in observation.items() if key != "name"}
            evidence_rows.append({"segment": signal.segment, "observation": name, **fields})
        for criterion in signal.confidence.criteria:
            criteria_rows.append(
                {
                    "segment": signal.segment,
                    "criterion": criterion.name,
                    "met": criterion.met,
                    "detail": criterion.detail,
                }
            )

    return {
        "signals": signals,
        "table": signals_to_frame(signals),
        "evidence": pd.DataFrame(evidence_rows),
        "criteria": pd.DataFrame(criteria_rows),
    }


def available_periods(tables: dict[str, pd.DataFrame], period: str = "week") -> list[pd.Timestamp]:
    """Complete periods a user may choose between, most recent first."""
    summary = summarise_by_period(tables, period=period)
    if summary.empty:
        return []
    return sorted(pd.to_datetime(summary[PERIOD_COLUMN]).unique(), reverse=True)


def data_quality_summary(data: dict) -> pd.DataFrame:
    """What cleaning changed on the way in, as a table."""
    audit = data.get("audit")
    if audit is None or audit.empty:
        return pd.DataFrame(columns=["table", "action", "rows", "detail"])
    return audit
