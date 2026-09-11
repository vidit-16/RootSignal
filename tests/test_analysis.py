from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.analysis import (
    COMPARISON_FORECAST,
    COMPARISON_PREVIOUS_PERIOD,
    COMPARISON_TARGET,
    VARIANCE_COLUMNS,
    add_period_column,
    calculate_trend,
    combine_variances,
    drop_partial_periods,
    latest_movement,
    period_calendar,
    rank_variances,
    summarise_by_period,
    variance_vs_forecast,
    variance_vs_previous_period,
    variance_vs_target,
)

MONDAY = "2026-01-05"
TUESDAY = "2026-01-06"


def make_tables(
    sales_rows: list[dict],
    order_rows: list[dict],
    target_rows: list[dict] | None = None,
) -> dict[str, pd.DataFrame]:
    """Build the minimum dimension set the consolidation layer needs."""
    return {
        "dim_date": pd.DataFrame(
            {
                "date": pd.to_datetime([MONDAY, TUESDAY]).date,
                "week": [2, 2],
                "month": [1, 1],
                "quarter": ["Q1", "Q1"],
                "year": [2026, 2026],
            }
        ),
        "dim_kam": pd.DataFrame({"kam_id": ["K1"], "kam_name": ["KAM One"]}),
        "dim_region": pd.DataFrame(
            {"region_code": ["BLR"], "city": ["Bengaluru"], "zone": ["South"]}
        ),
        "dim_sku": pd.DataFrame(
            {
                "sku_id": ["S1", "S2"],
                "sku_name": ["Apple", "Banana"],
                "category": ["Fruits", "Fruits"],
                "sub_category": ["Standard", "Premium"],
                "pack_size_kg": [1.0, 0.5],
                "unit_cost": [70.0, 80.0],
                "list_price": [100.0, 120.0],
                "active_flag": [True, True],
            }
        ),
        "dim_customer": pd.DataFrame(
            {
                "customer_id": ["C1"],
                "customer_name": ["Customer One"],
                "customer_type": ["Retail Chain"],
                "channel": ["Modern Trade"],
                "region_code": ["BLR"],
                "city": ["Bengaluru"],
                "zone": ["South"],
                "kam_id": ["K1"],
            }
        ),
        "fact_sales": pd.DataFrame(sales_rows),
        "fact_orders": pd.DataFrame(order_rows),
        "fact_inventory": pd.DataFrame(
            columns=[
                "date", "sku_id", "warehouse", "region_code", "opening_stock",
                "received_units", "available_stock", "ordered_units",
                "fulfilled_units", "stockout_flag",
            ]
        ),
        "fact_targets": pd.DataFrame(
            target_rows
            if target_rows is not None
            else {
                "date": [],
                "region_code": [],
                "category": [],
                "channel": [],
                "sales_target": [],
                "order_target": [],
                "fill_rate_target": [],
            }
        ),
    }


def sale(date: str, order_id: str, sku: str, units: int, price: float, net: float) -> dict:
    return {
        "order_id": order_id,
        "date": pd.Timestamp(date).date(),
        "customer_id": "C1",
        "region_code": "BLR",
        "channel": "Modern Trade",
        "kam_id": "K1",
        "sku_id": sku,
        "sales_type": "PRIMARY",
        "units": units,
        "unit_price": price,
        "discount_pct": 0.0,
        "net_sales": net,
    }


def order(date: str, order_id: str, sku: str, ordered: int, fulfilled: int) -> dict:
    return {
        "order_id": order_id,
        "date": pd.Timestamp(date).date(),
        "customer_id": "C1",
        "region_code": "BLR",
        "channel": "Modern Trade",
        "sku_id": sku,
        "ordered_units": ordered,
        "fulfilled_units": fulfilled,
        "cancelled_units": ordered - fulfilled,
        "order_status": "Completed",
        "sales_type": "PRIMARY",
    }


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


def test_week_periods_start_on_monday() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-05", "2026-01-11", "2026-01-12"])})
    labelled = add_period_column(frame, "week")
    assert labelled["period_start"].tolist() == [
        pd.Timestamp("2026-01-05"),
        pd.Timestamp("2026-01-05"),  # Sunday still belongs to the Monday week
        pd.Timestamp("2026-01-12"),
    ]


def test_month_periods_start_on_the_first() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-31", "2026-02-01"])})
    labelled = add_period_column(frame, "month")
    assert labelled["period_start"].tolist() == [
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-02-01"),
    ]


def test_period_calendar_flags_partial_periods_at_both_ends() -> None:
    """The sample range starts mid-week and ends mid-month."""
    weeks = period_calendar("week", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-01"))
    assert len(weeks) == 9
    assert not weeks.iloc[0]["is_complete"]  # starts Thursday
    assert weeks.iloc[-1]["is_complete"]  # ends Sunday

    months = period_calendar("month", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-01"))
    assert months["is_complete"].tolist() == [True, True, False]  # March has one day


def test_period_calendar_rejects_unknown_period() -> None:
    with pytest.raises(ValueError, match="Unsupported period"):
        period_calendar("fortnight", pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31"))


def test_drop_partial_periods_requires_completeness_metadata() -> None:
    with pytest.raises(ValueError, match="is_complete"):
        drop_partial_periods(pd.DataFrame({"period_start": []}))


# --------------------------------------------------------------------------
# Period summaries
# --------------------------------------------------------------------------


def test_period_summary_does_not_multiply_multi_sku_orders() -> None:
    """One order across two SKUs is one order at the period grain."""
    tables = make_tables(
        [sale(MONDAY, "O1", "S1", 2, 100.0, 200.0), sale(MONDAY, "O1", "S2", 3, 120.0, 360.0)],
        [order(MONDAY, "O1", "S1", 4, 2), order(MONDAY, "O1", "S2", 3, 3)],
    )
    summary = summarise_by_period(tables, period="day")
    row = summary.iloc[0]

    assert len(summary) == 1
    assert row["order_count"] == 1
    assert row["sales_order_count"] == 1
    assert row["sales_units"] == 5
    assert row["net_sales"] == 560.0


def test_period_ratios_are_volume_weighted_not_averaged() -> None:
    """A weekly fill rate must come from summed units, not from averaged days.

    Day one fills 50 of 100 units and day two fills 1 of 1. The correct weekly
    fill rate is 51/101, not the mean of 0.5 and 1.0.
    """
    tables = make_tables(
        [sale(MONDAY, "O1", "S1", 50, 100.0, 5000.0), sale(TUESDAY, "O2", "S1", 1, 100.0, 100.0)],
        [order(MONDAY, "O1", "S1", 100, 50), order(TUESDAY, "O2", "S1", 1, 1)],
    )
    weekly = summarise_by_period(tables, period="week", include_partial_periods=True)
    row = weekly.iloc[0]

    assert len(weekly) == 1
    assert row["ordered_units"] == 101
    assert row["fulfilled_units"] == 51
    assert row["fill_rate"] == pytest.approx(round(51 / 101, 4))
    assert row["fill_rate"] != pytest.approx(0.75)  # the averaged-days answer


def test_period_summary_drops_partial_periods_by_default() -> None:
    """Two days do not make a week, and must not be compared against one."""
    tables = make_tables(
        [sale(MONDAY, "O1", "S1", 1, 100.0, 100.0)],
        [order(MONDAY, "O1", "S1", 1, 1)],
    )
    assert summarise_by_period(tables, period="week").empty
    assert len(summarise_by_period(tables, period="week", include_partial_periods=True)) == 1


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------


def make_period_summary() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period_start": pd.to_datetime(
                ["2026-01-05", "2026-01-12", "2026-01-05", "2026-01-12"]
            ),
            "region_code": ["BLR", "BLR", "MUM", "MUM"],
            "period_type": ["week"] * 4,
            "net_sales": [100.0, 120.0, 200.0, 180.0],
        }
    )


def test_trend_reports_absolute_and_percentage_movement_within_segments() -> None:
    trend = calculate_trend(make_period_summary(), "net_sales", group_by=["region_code"])

    blr = trend[trend["region_code"] == "BLR"].iloc[0]
    mum = trend[trend["region_code"] == "MUM"].iloc[0]

    assert len(trend) == 2  # first period of each segment has nothing to compare against
    assert blr["absolute_change"] == 20.0
    assert blr["pct_change"] == pytest.approx(0.2)
    assert mum["absolute_change"] == -20.0
    assert mum["pct_change"] == pytest.approx(-0.1)
    assert blr["previous_period_start"] == pd.Timestamp("2026-01-05")


def test_trend_percentage_is_undefined_against_a_zero_baseline() -> None:
    frame = pd.DataFrame(
        {
            "period_start": pd.to_datetime(["2026-01-05", "2026-01-12"]),
            "net_sales": [0.0, 50.0],
        }
    )
    trend = calculate_trend(frame, "net_sales")
    assert trend.iloc[0]["absolute_change"] == 50.0
    assert pd.isna(trend.iloc[0]["pct_change"])


def test_trend_rejects_period_column_in_group_by() -> None:
    with pytest.raises(ValueError, match="must not appear in group_by"):
        calculate_trend(make_period_summary(), "net_sales", group_by=["period_start"])


def test_trend_rejects_unknown_metric() -> None:
    with pytest.raises(ValueError, match="no metric"):
        calculate_trend(make_period_summary(), "profit")


def test_latest_movement_returns_one_row_per_segment() -> None:
    trend = calculate_trend(make_period_summary(), "net_sales", group_by=["region_code"])
    latest = latest_movement(trend, group_by=["region_code"])
    assert len(latest) == 2
    assert set(latest["region_code"]) == {"BLR", "MUM"}


# --------------------------------------------------------------------------
# Variance
# --------------------------------------------------------------------------


def make_targets() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(MONDAY).date(), pd.Timestamp(TUESDAY).date()],
            "region_code": ["BLR", "BLR"],
            "category": ["Fruits", "Fruits"],
            "channel": ["Modern Trade", "Modern Trade"],
            "sales_target": [100.0, 300.0],
            "order_target": [1, 3],
            "fill_rate_target": [0.90, 0.80],
        }
    )


def test_variance_vs_target_compares_at_a_matching_grain() -> None:
    summary = pd.DataFrame(
        {
            "period_start": pd.to_datetime([MONDAY]),
            "period_type": ["day"],
            "net_sales": [80.0],
        }
    )
    targets = make_targets()
    variance = variance_vs_target(
        summary, targets[targets["date"] == pd.Timestamp(MONDAY).date()], "net_sales", "sales_target"
    )
    row = variance.iloc[0]

    assert row["actual"] == 80.0
    assert row["comparison_value"] == 100.0
    assert row["variance"] == -20.0
    assert row["variance_pct"] == pytest.approx(-0.2)
    assert row["comparison_type"] == COMPARISON_TARGET


def test_quantity_targets_are_summed_but_rate_targets_are_averaged() -> None:
    """A weekly sales target adds up; a weekly fill-rate target does not.

    Summing two daily 0.9 and 0.8 rate targets would produce a 1.7 target,
    which is not a rate at all.
    """
    summary = pd.DataFrame(
        {
            "period_start": pd.to_datetime(["2026-01-05"] * 1),
            "period_type": ["week"],
            "net_sales": [350.0],
            "fill_rate": [0.85],
        }
    )
    targets = make_targets()

    sales_variance = variance_vs_target(
        summary, targets, "net_sales", "sales_target", period="week"
    )
    rate_variance = variance_vs_target(
        summary, targets, "fill_rate", "fill_rate_target", period="week"
    )

    assert sales_variance.iloc[0]["comparison_value"] == 400.0  # 100 + 300
    assert rate_variance.iloc[0]["comparison_value"] == pytest.approx(0.85)  # mean of 0.9, 0.8


def test_variance_vs_target_rejects_an_undefined_target_column() -> None:
    summary = pd.DataFrame({"period_start": pd.to_datetime([MONDAY]), "net_sales": [1.0]})
    targets = make_targets().assign(margin_target=1.0)
    with pytest.raises(ValueError, match="No aggregation defined"):
        variance_vs_target(summary, targets, "net_sales", "margin_target")


def test_variance_vs_target_rejects_a_grain_targets_cannot_support() -> None:
    summary = pd.DataFrame(
        {"period_start": pd.to_datetime([MONDAY]), "kam_id": ["K1"], "net_sales": [1.0]}
    )
    with pytest.raises(ValueError, match="missing dimensions"):
        variance_vs_target(summary, make_targets(), "net_sales", "sales_target", group_by=["kam_id"])


def test_variance_vs_forecast_compares_only_shared_dates() -> None:
    actual = pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-01-05", "2026-01-06"]))
    forecast = pd.Series([90.0, 95.0, 99.0], index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]))

    variance = variance_vs_forecast(actual, forecast, "net_sales")

    assert len(variance) == 2  # the unmatched forecast day is not evidence
    assert variance.iloc[0]["variance"] == 10.0
    assert variance.iloc[1]["variance"] == 15.0
    assert set(variance["comparison_type"]) == {COMPARISON_FORECAST}


def test_variance_vs_forecast_requires_overlapping_dates() -> None:
    actual = pd.Series([1.0], index=pd.to_datetime(["2026-01-05"]))
    forecast = pd.Series([1.0], index=pd.to_datetime(["2026-02-05"]))
    with pytest.raises(ValueError, match="share no dates"):
        variance_vs_forecast(actual, forecast, "net_sales")


def test_all_comparison_types_share_one_schema() -> None:
    """Downstream layers consume variance without knowing its origin.

    If the three producers drifted apart, driver decomposition and the signal
    layer would need to special-case each one.
    """
    summary = pd.DataFrame(
        {
            "period_start": pd.to_datetime([MONDAY, TUESDAY]),
            "period_type": ["day", "day"],
            "net_sales": [80.0, 260.0],
        }
    )
    previous = variance_vs_previous_period(summary, "net_sales")
    target = variance_vs_target(summary, make_targets(), "net_sales", "sales_target")
    forecast = variance_vs_forecast(
        pd.Series([80.0], index=pd.to_datetime([MONDAY])),
        pd.Series([70.0], index=pd.to_datetime([MONDAY])),
        "net_sales",
    )

    for frame in (previous, target, forecast):
        assert list(frame.columns) == VARIANCE_COLUMNS

    combined = combine_variances(previous, target, forecast)
    assert set(combined["comparison_type"]) == {
        COMPARISON_PREVIOUS_PERIOD,
        COMPARISON_TARGET,
        COMPARISON_FORECAST,
    }


def test_combine_variances_rejects_a_frame_missing_the_schema() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        combine_variances(pd.DataFrame({"metric": ["net_sales"]}))


def test_rank_variances_orders_by_gap_size() -> None:
    variances = pd.DataFrame(
        {
            "metric": ["net_sales"] * 3,
            "segment": ["A", "B", "C"],
            "variance": [-50.0, 10.0, -200.0],
        }
    )
    worst = rank_variances(variances, direction="negative", top_n=2)
    assert worst["segment"].tolist() == ["C", "A"]

    best = rank_variances(variances, direction="positive", top_n=5)
    assert best["segment"].tolist() == ["B"]

    by_size = rank_variances(variances, direction="absolute", top_n=3)
    assert by_size["segment"].tolist() == ["C", "A", "B"]


def test_rank_variances_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        rank_variances(pd.DataFrame({"variance": [1.0]}), direction="sideways")


# --------------------------------------------------------------------------
# Integration with the cleaned pipeline
# --------------------------------------------------------------------------


def test_weekly_summary_covers_only_complete_weeks(cleaned_dataset) -> None:
    """The sample range starts on a Thursday, so its first week is partial."""
    tables = cleaned_dataset.tables
    complete = summarise_by_period(tables, period="week")
    with_partial = summarise_by_period(tables, period="week", include_partial_periods=True)

    assert len(complete) == 8
    assert len(with_partial) == 9
    assert complete["is_complete"].all()


def test_partial_first_week_would_distort_week_over_week_movement(cleaned_dataset) -> None:
    """Concrete evidence for why partial periods are excluded.

    The first week holds four days. Comparing it against a full week reports a
    change in calendar coverage as though it were a change in trade.
    """
    tables = cleaned_dataset.tables
    misleading = calculate_trend(
        summarise_by_period(tables, period="week", include_partial_periods=True), "net_sales"
    )
    honest = calculate_trend(summarise_by_period(tables, period="week"), "net_sales")

    assert misleading.iloc[0]["pct_change"] > 0.5  # a fake surge out of the short week
    assert honest["pct_change"].abs().max() < 0.2  # real weekly movement is modest


def test_fill_rate_collapse_in_the_disrupted_segment_is_detected(cleaned_dataset) -> None:
    """The seeded supply scenario must be visible to variance analysis.

    From 2026-02-18 the Bengaluru fruit and vegetable segments fulfil a far
    smaller share of demand. Weekly fill rate should fall clearly below both the
    prior week and the service-level target once the disruption lands.
    """
    tables = cleaned_dataset.tables
    weekly = summarise_by_period(tables, period="week", group_by=["region_code", "category"])

    trend = calculate_trend(weekly, "fill_rate", group_by=["region_code", "category"])
    disrupted = trend[
        (trend["region_code"] == "BLR")
        & (trend["category"].isin(["Fruits", "Vegetables"]))
        & (trend["period_start"] >= pd.Timestamp("2026-02-16"))
    ]
    assert not disrupted.empty
    assert (disrupted["absolute_change"] < 0).all()

    against_target = variance_vs_target(
        weekly,
        tables["fact_targets"],
        "fill_rate",
        "fill_rate_target",
        period="week",
        group_by=["region_code", "category"],
    )
    late_disruption = against_target[
        (against_target["region_code"] == "BLR")
        & (against_target["category"].isin(["Fruits", "Vegetables"]))
        & (against_target["period_start"] >= pd.Timestamp("2026-02-16"))
    ]
    assert (late_disruption["variance"] < 0).all()

    # Before the disruption the same segments were meeting the target.
    early = against_target[
        (against_target["region_code"] == "BLR")
        & (against_target["category"].isin(["Fruits", "Vegetables"]))
        & (against_target["period_start"] < pd.Timestamp("2026-02-16"))
    ]
    assert (early["variance"] > 0).mean() > 0.5
