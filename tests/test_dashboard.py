from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.analysis import PERIOD_COLUMN
from rootsignal.dashboard import (
    available_periods,
    data_quality_summary,
    forecast_views,
    load_business_data,
    overview,
    sales_views,
    signal_views,
    supply_views,
)


# --------------------------------------------------------------------------
# The data layer is testable without a browser
# --------------------------------------------------------------------------


def test_dashboard_views_need_no_streamlit() -> None:
    """The pages hold layout; the analysis lives where it can be tested.

    If preparing a view required Streamlit, none of it could be checked without
    starting a web server, and the pages would quietly accumulate analysis.
    """
    import sys

    import rootsignal.dashboard.views as views

    assert "streamlit" not in sys.modules or "streamlit" not in dir(views)
    assert not hasattr(views, "st")


def test_loading_keeps_the_cleaning_audit_with_the_data(cleaned_dataset) -> None:
    """Cleaned figures should not be presented as though they arrived that way."""
    data = load_business_data("data/raw/generated")

    assert set(data) == {"tables", "audit", "quarantined", "raw_row_counts"}
    assert not data["audit"].empty
    assert len(data["tables"]) == 10
    assert data["raw_row_counts"]["fact_sales"] > len(data["tables"]["fact_sales"])


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------


def test_overview_reports_the_latest_complete_period(cleaned_dataset) -> None:
    """A short week must never be presented as the headline."""
    state = overview(cleaned_dataset.tables, period="week")

    assert state["available"]
    assert state["comparison_period"] < state["period"]
    for metric in ("net_sales", "sales_units", "order_count", "fill_rate", "aov"):
        assert metric in state["headline"]
        assert state["headline"][metric]["value"] > 0


def test_overview_movement_matches_the_underlying_summary(cleaned_dataset) -> None:
    """The dashboard renders results; it must not derive its own."""
    tables = cleaned_dataset.tables
    state = overview(tables, period="week")
    summary = state["summary"]

    current = summary.loc[pd.to_datetime(summary[PERIOD_COLUMN]) == state["period"]].iloc[0]
    entry = state["headline"]["net_sales"]

    assert entry["value"] == pytest.approx(current["net_sales"])
    assert entry["change"] == pytest.approx(entry["value"] - entry["previous"])


def test_overview_is_unavailable_without_two_periods() -> None:
    """Saying so beats rendering a comparison against nothing."""
    empty = {"fact_sales": pd.DataFrame(), "fact_orders": pd.DataFrame()}
    with pytest.raises(Exception):  # noqa: B017 - any failure is acceptable here
        overview(empty)


# --------------------------------------------------------------------------
# Page views
# --------------------------------------------------------------------------


def test_sales_views_cover_every_offered_breakdown(cleaned_dataset) -> None:
    views = sales_views(cleaned_dataset.tables, period="week")

    assert {"daily", "by_region", "by_category", "by_channel", "by_kam", "mix", "versus_plan"} <= set(views)
    for key in ("by_region", "by_category", "by_channel", "by_kam"):
        assert not views[key].empty, key


def test_each_sales_breakdown_totals_the_same_trade(cleaned_dataset) -> None:
    """Different cuts of one business must agree on its size.

    They are alternative views, never additive with one another.
    """
    views = sales_views(cleaned_dataset.tables, period="week")
    totals = {
        key: round(float(views[key]["net_sales"].sum()), 2)
        for key in ("by_region", "by_category", "by_channel", "by_kam")
    }
    assert len(set(totals.values())) == 1, totals


def test_supply_series_are_built_at_the_grain_they_are_shown(cleaned_dataset) -> None:
    """A region line must be one point per region per period.

    Plotting a region-by-category frame as a regional series would draw several
    points per period and render a zigzag rather than a trend. A fill rate must
    also be rebuilt from summed units at the level it is displayed, never
    averaged up from a finer one.
    """
    views = supply_views(cleaned_dataset.tables, period="week")
    by_region = views["by_region"]

    expected = by_region[PERIOD_COLUMN].nunique() * by_region["region_code"].nunique()
    assert len(by_region) == expected

    row = by_region.iloc[0]
    assert row["fill_rate"] == pytest.approx(
        round(row["fulfilled_units"] / row["ordered_units"], 4)
    )


def test_inventory_covers_the_same_periods_as_the_commercial_view(cleaned_dataset) -> None:
    """Charts shown side by side must not disagree about which weeks exist.

    Inventory summaries do not drop partial periods on their own, so without
    alignment the stock chart would carry a final short week the sales chart
    beside it excludes.
    """
    views = supply_views(cleaned_dataset.tables, period="week")

    commercial = set(pd.to_datetime(views["by_region"][PERIOD_COLUMN]))
    inventory = set(pd.to_datetime(views["inventory_by_region"][PERIOD_COLUMN]))
    assert inventory <= commercial


def test_supply_sku_view_reports_unserved_demand(cleaned_dataset) -> None:
    views = supply_views(cleaned_dataset.tables, period="week")
    by_sku = views["by_sku"]

    assert {"sku_name", "category", "unfulfilled_units"} <= set(by_sku.columns)
    assert (by_sku["unfulfilled_units"] >= 0).all()
    assert by_sku["unfulfilled_units"].sum() > 0


def test_forecast_views_cover_every_metric_out_of_sample(cleaned_dataset) -> None:
    views = forecast_views(cleaned_dataset.tables)

    assert set(views["accuracy"]) == {"net_sales", "units", "orders"}
    for metric, summary in views["accuracy"].items():
        assert summary["wape"].is_monotonic_increasing, metric
        backtest = views["backtests"][metric]
        assert (backtest["origin_date"] < backtest["date"]).all(), metric


def test_signal_views_return_reasoning_beside_conclusions(cleaned_dataset) -> None:
    """A page must not be able to show a confidence level without its criteria."""
    views = signal_views(
        cleaned_dataset.tables,
        current_period="2026-02-23",
        comparison_period="2026-02-09",
        top_n=3,
    )

    assert len(views["signals"]) == 3
    assert len(views["table"]) == 3
    assert not views["evidence"].empty
    # Six named criteria per signal, every time.
    assert len(views["criteria"]) == 3 * 6


def test_signal_evidence_names_each_observation_once(cleaned_dataset) -> None:
    """The observation carries its own name; the table should not repeat it."""
    views = signal_views(
        cleaned_dataset.tables, current_period="2026-02-23", comparison_period="2026-02-09"
    )
    evidence = views["evidence"]

    assert "observation" in evidence.columns
    assert "name" not in evidence.columns


def test_a_strict_confidence_floor_can_return_nothing(cleaned_dataset) -> None:
    """The dashboard must be able to report that nothing stands out."""
    quiet = signal_views(
        cleaned_dataset.tables,
        current_period="2026-01-19",
        comparison_period="2026-01-12",
        min_confidence="high",
    )
    assert quiet["signals"] == []
    assert quiet["table"].empty


def test_available_periods_are_offered_most_recent_first(cleaned_dataset) -> None:
    periods = available_periods(cleaned_dataset.tables, period="week")
    assert len(periods) == 8
    assert periods == sorted(periods, reverse=True)


def test_data_quality_summary_survives_a_clean_dataset() -> None:
    assert data_quality_summary({"audit": pd.DataFrame()}).empty
    assert list(data_quality_summary({}).columns) == ["table", "action", "rows", "detail"]


# --------------------------------------------------------------------------
# Charts, where the plotting library is available
# --------------------------------------------------------------------------


def chart_module():
    pytest.importorskip("plotly", reason="plotly ships with the dashboard extra")
    import sys
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    from components import charts

    return charts


def test_categorical_colours_are_a_fixed_order() -> None:
    """Colour follows the entity, so filtering must not repaint the survivors."""
    charts = chart_module()
    assert len(charts.CATEGORICAL) == 8
    assert charts.series_colour(0) == charts.CATEGORICAL[0]
    assert charts.series_colour(1) == charts.CATEGORICAL[1]


def test_status_colours_are_never_categorical_slots() -> None:
    """A status colour must not be able to impersonate a series."""
    charts = chart_module()
    assert not set(charts.STATUS.values()) & set(charts.CATEGORICAL)


def test_series_are_capped_rather_than_the_palette_extended() -> None:
    """A ninth series folds into "Other"; a generated hue would be unvalidated."""
    charts = chart_module()
    frame = pd.DataFrame(
        {"segment": [f"S{i}" for i in range(12)], "net_sales": list(range(12, 0, -1))}
    )
    folded = charts.fold_small_series(frame, "segment", "net_sales")

    assert folded["segment"].nunique() == charts.MAX_SERIES
    assert "Other" in set(folded["segment"])
    assert folded["net_sales"].sum() == frame["net_sales"].sum()


def test_service_status_always_returns_a_label_with_its_colour() -> None:
    """A status shown as colour alone is unreadable to many viewers."""
    charts = chart_module()
    for fill_rate, target in ((0.99, 0.93), (0.91, 0.93), (0.85, 0.93), (0.60, 0.93), (None, 0.93)):
        label, colour = charts.service_status(fill_rate, target)
        assert label and colour.startswith("#")


def test_axis_labels_come_from_the_shared_map() -> None:
    """A chart axis, a table header and a report column must agree on names."""
    charts = chart_module()
    from rootsignal.presentation import label_for

    for column in ("net_sales", "region_code", "kam_id", "fill_rate"):
        assert charts.prettify(column) == label_for(column)
    assert charts.prettify("region_code") == "Region"  # not "Region code"


# --------------------------------------------------------------------------
# Reader-facing language
# --------------------------------------------------------------------------


def test_identifier_codes_become_the_names_the_data_already_holds(cleaned_dataset) -> None:
    """A tool built for account managers should be able to name them.

    The customer master carries the names; nothing but habit keeps K001 on the
    screen.
    """
    from rootsignal.presentation import build_name_lookup

    tables = cleaned_dataset.tables
    lookup = build_name_lookup(tables)

    assert lookup["K001"] == "Aarav Mehta"
    assert lookup["BLR"] == "Bengaluru"
    assert lookup["DEL"] == "Delhi NCR"


def test_composite_segments_are_rendered_in_names(cleaned_dataset) -> None:
    from rootsignal.presentation import build_name_lookup, humanise_segment

    lookup = build_name_lookup(cleaned_dataset.tables)
    assert humanise_segment("BLR | Vegetables", lookup) == "Bengaluru · Vegetables"


def test_unknown_codes_are_left_visible_rather_than_blanked() -> None:
    """A missing lookup should be obvious, not silently erase a row's identity."""
    from rootsignal.presentation import humanise_segment

    assert humanise_segment("ZZZ | Fruits", {"BLR": "Bengaluru"}) == "ZZZ · Fruits"


def test_internal_tokens_are_unchanged_by_the_display_layer(cleaned_dataset) -> None:
    """Translation happens on the way out only.

    Downstream code and tests depend on a stable pattern token, so the label a
    reader sees must not replace the value the system reasons about.
    """
    from rootsignal.presentation import describe_pattern

    views = signal_views(
        cleaned_dataset.tables, current_period="2026-02-23", comparison_period="2026-02-09"
    )
    signal = views["signals"][0]

    assert signal.pattern.pattern in {
        "fulfilment_constraint", "demand_softness", "portfolio_mix_shift", "unclassified"
    }
    assert describe_pattern(signal.pattern.pattern) != signal.pattern.pattern


def test_generated_prose_shows_places_rather_than_codes(cleaned_dataset) -> None:
    """The signal layer builds statements around the segment key; readers see names."""
    from rootsignal.presentation import build_name_lookup, humanise_statement

    lookup = build_name_lookup(cleaned_dataset.tables)
    text = "Review inventory availability for BLR | Fruits, starting with the SKUs."
    rendered = humanise_statement(text, "BLR | Fruits", lookup)

    assert "BLR" not in rendered
    assert "Bengaluru · Fruits" in rendered


def test_every_displayed_label_avoids_raw_schema_names(cleaned_dataset) -> None:
    """No underscore-separated key should reach a reader."""
    from rootsignal.presentation import build_name_lookup, for_display

    tables = cleaned_dataset.tables
    views = sales_views(tables, period="week")
    displayed = for_display(views["by_region"], lookup=build_name_lookup(tables))

    for column in displayed.columns:
        assert "_" not in column, column


def test_measure_names_are_translated_as_values_too(cleaned_dataset) -> None:
    """Some columns hold measure names as values, and they need the same map."""
    from rootsignal.presentation import for_display

    views = signal_views(
        cleaned_dataset.tables, current_period="2026-02-23", comparison_period="2026-02-09"
    )
    displayed = for_display(views["evidence"])

    measures = set(displayed["Measure"])
    assert "ordered_units" not in measures
    assert "Units ordered" in measures


def test_every_confidence_criterion_has_a_plain_label() -> None:
    """A criterion called movement_stands_out explains nothing to a reader."""
    from rootsignal.signals.confidence import assess_confidence
    from rootsignal.presentation import CRITERION_LABELS, describe_criterion

    assessment = assess_confidence(-0.2, 0.05, 3, 0.4, 4, False, 0.65)
    for criterion in assessment.criteria:
        assert criterion.name in CRITERION_LABELS, criterion.name
        assert describe_criterion(criterion.name) != criterion.name


def test_every_forecasting_method_has_a_sayable_name() -> None:
    from rootsignal.forecasting import default_model_suite
    from rootsignal.presentation import MODEL_LABELS, describe_model

    for model in default_model_suite():
        assert model.name in MODEL_LABELS, model.name
        assert describe_model(model.name) != model.name


def test_jargon_carries_a_plain_definition() -> None:
    """Terms a reader may not have met are explained rather than assumed."""
    from rootsignal.presentation import GLOSSARY, glossary_for, label_for

    for column in ("fill_rate", "wape", "impact", "confidence", "primary_sales"):
        assert label_for(column) in GLOSSARY, column
    assert glossary_for(["Fill rate"])["Fill rate"]
