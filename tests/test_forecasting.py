from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rootsignal.forecasting import (
    MovingAverageForecaster,
    NaiveForecaster,
    SeasonalMeanForecaster,
    SeasonalNaiveForecaster,
    SimpleExponentialSmoothingForecaster,
    bias,
    build_daily_series,
    compare_against_baseline,
    default_model_suite,
    evaluate_holdout,
    extract_metric,
    holdout_split,
    mae,
    mape,
    rmse,
    rolling_origin_evaluate,
    score_forecast,
    summarise_backtest,
    wape,
)


def make_series(values: list[float], start: str = "2026-01-01") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=index, dtype=float)


# --------------------------------------------------------------------------
# Series construction
# --------------------------------------------------------------------------


def test_daily_series_counts_multi_sku_orders_once() -> None:
    """One order spanning two SKUs is one order, but two sales lines."""
    sales = pd.DataFrame(
        {
            "order_id": ["O1", "O1", "O2"],
            "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
            "units": [2, 3, 4],
            "net_sales": [200.0, 360.0, 400.0],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": ["O1", "O1", "O2"],
            "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
        }
    )
    daily = build_daily_series(sales, orders)

    assert daily.loc["2026-01-01", "orders"] == 1
    assert daily.loc["2026-01-01", "units"] == 5
    assert daily.loc["2026-01-01", "net_sales"] == 560.0
    assert daily.loc["2026-01-02", "orders"] == 1


def test_daily_series_fills_calendar_gaps_with_zero() -> None:
    """A day with no trade is an observation, not an absence.

    Dropping it would shorten the seasonal cycle and silently misalign every
    weekly position downstream.
    """
    sales = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "date": ["2026-01-01", "2026-01-04"],
            "units": [1, 1],
            "net_sales": [10.0, 10.0],
        }
    )
    daily = build_daily_series(sales, metrics=["net_sales"])

    assert len(daily) == 4
    assert daily.loc["2026-01-02", "net_sales"] == 0.0
    assert daily.loc["2026-01-03", "net_sales"] == 0.0


def test_daily_series_rejects_unknown_metric() -> None:
    sales = pd.DataFrame({"order_id": ["O1"], "date": ["2026-01-01"], "units": [1], "net_sales": [1.0]})
    with pytest.raises(ValueError, match="Unsupported forecast metrics"):
        build_daily_series(sales, metrics=["profit"])


def test_orders_metric_requires_order_fact() -> None:
    sales = pd.DataFrame({"order_id": ["O1"], "date": ["2026-01-01"], "units": [1], "net_sales": [1.0]})
    with pytest.raises(ValueError, match="fact_orders is required"):
        build_daily_series(sales, orders=None, metrics=["orders"])


# --------------------------------------------------------------------------
# Model behaviour, against hand-computed values
# --------------------------------------------------------------------------


def test_naive_carries_last_value_forward() -> None:
    forecast = NaiveForecaster().fit_predict(make_series(list(range(1, 15))), 3)
    assert forecast.tolist() == [14.0, 14.0, 14.0]
    assert forecast.index[0] == pd.Timestamp("2026-01-15")


def test_seasonal_naive_repeats_last_season_and_wraps() -> None:
    forecast = SeasonalNaiveForecaster(7).fit_predict(make_series(list(range(1, 15))), 10)
    assert forecast.tolist() == [8, 9, 10, 11, 12, 13, 14, 8, 9, 10]


def test_seasonal_mean_averages_each_position_from_end_of_history() -> None:
    """Position 0 must be the next day after history, whatever the start date."""
    forecast = SeasonalMeanForecaster(7).fit_predict(make_series(list(range(1, 15))), 7)
    # Positions pair index 0 with 7, 1 with 8, and so on: (1+8)/2, (2+9)/2, ...
    assert forecast.tolist() == [4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5]


def test_moving_average_uses_only_the_window() -> None:
    forecast = MovingAverageForecaster(7).fit_predict(make_series(list(range(1, 15))), 2)
    assert forecast.tolist() == [11.0, 11.0]  # mean of 8..14


def test_ses_with_alpha_one_reduces_to_last_value() -> None:
    forecast = SimpleExponentialSmoothingForecaster(alpha=1.0).fit_predict(
        make_series(list(range(1, 15))), 2
    )
    assert forecast.tolist() == [14.0, 14.0]


def test_ses_selects_alpha_from_training_history_only() -> None:
    model = SimpleExponentialSmoothingForecaster()
    model.fit(make_series([10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0, 15.0]))
    assert 0.0 < model.selected_alpha <= 1.0


def test_models_are_deterministic() -> None:
    """Same history, same forecast. Forecasts are used as evidence downstream."""
    history = make_series([float(v) for v in [12, 19, 14, 22, 17, 25, 20] * 4])
    for model in default_model_suite():
        first = model.fit_predict(history, 7)
        second = model.fit_predict(history, 7)
        pd.testing.assert_series_equal(first, second)


# --------------------------------------------------------------------------
# History validation
# --------------------------------------------------------------------------


def test_forecaster_rejects_gapped_history() -> None:
    gapped = pd.Series(
        [1.0, 2.0, 3.0],
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]),
    )
    with pytest.raises(ValueError, match="gap-free daily series"):
        NaiveForecaster().fit(gapped)


def test_forecaster_rejects_missing_values() -> None:
    with pytest.raises(ValueError, match="missing values"):
        NaiveForecaster().fit(make_series([1.0, np.nan, 3.0]))


def test_forecaster_rejects_non_datetime_index() -> None:
    with pytest.raises(ValueError, match="indexed by date"):
        NaiveForecaster().fit(pd.Series([1.0, 2.0]))


def test_seasonal_model_rejects_history_shorter_than_one_season() -> None:
    with pytest.raises(ValueError, match="at least 7 observations"):
        SeasonalNaiveForecaster(7).fit(make_series([1.0, 2.0, 3.0]))


def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1 day"):
        NaiveForecaster().fit_predict(make_series([1.0, 2.0]), 0)


# --------------------------------------------------------------------------
# Accuracy metrics, against hand-computed values
# --------------------------------------------------------------------------


def test_metrics_match_hand_computed_values() -> None:
    actual = make_series([10.0, 20.0, 30.0])
    forecast = make_series([12.0, 18.0, 33.0])

    assert mae(actual, forecast) == pytest.approx(7 / 3)
    assert rmse(actual, forecast) == pytest.approx(np.sqrt(17 / 3))
    assert wape(actual, forecast) == pytest.approx(7 / 60)
    assert mape(actual, forecast) == pytest.approx(np.mean([0.2, 0.1, 0.1]))
    assert bias(actual, forecast) == pytest.approx(1.0)


def test_mape_is_undefined_when_an_actual_is_near_zero() -> None:
    """MAPE divides by each actual, so one near-zero day would dominate it.

    Reporting NaN is honest; reporting a huge percentage would not be.
    """
    actual = make_series([100.0, 100.0, 0.0])
    forecast = make_series([100.0, 100.0, 5.0])

    assert np.isnan(mape(actual, forecast))
    assert not np.isnan(wape(actual, forecast))  # WAPE stays well defined


def test_wape_is_undefined_when_all_actuals_are_zero() -> None:
    actual = make_series([0.0, 0.0])
    assert np.isnan(wape(actual, make_series([1.0, 1.0])))


def test_score_forecast_rejects_misaligned_dates() -> None:
    actual = make_series([1.0, 2.0], start="2026-01-01")
    forecast = make_series([1.0, 2.0], start="2026-02-01")
    with pytest.raises(ValueError, match="same dates"):
        score_forecast(actual, forecast)


# --------------------------------------------------------------------------
# Backtesting
# --------------------------------------------------------------------------


def test_holdout_split_reserves_the_final_window() -> None:
    train, test = holdout_split(make_series([float(v) for v in range(10)]), 3)
    assert len(train) == 7
    assert test.tolist() == [7.0, 8.0, 9.0]
    assert train.index.max() < test.index.min()


def test_holdout_split_requires_training_history() -> None:
    with pytest.raises(ValueError, match="no training history"):
        holdout_split(make_series([1.0, 2.0, 3.0]), 3)


def test_rolling_origin_does_not_leak_future_observations() -> None:
    """A fold's forecast must not change when data after its origin changes.

    This is the property that makes every accuracy number trustworthy, so it is
    asserted directly rather than inferred from the code.
    """
    initial_train = 28
    values = [float(v) for v in [12, 19, 14, 22, 17, 25, 20] * 8]
    original = make_series(values)

    # Corrupt everything from the first origin onward. Fold 1 trains only on
    # days before that point, so its forecast must be completely unaffected.
    # If the split ever let training reach into the window being scored, these
    # values would change the forecast immediately.
    corrupted_values = list(values)
    for position in range(initial_train, len(corrupted_values)):
        corrupted_values[position] = 99999.0
    corrupted = make_series(corrupted_values)

    before = rolling_origin_evaluate(original, horizon=7, initial_train=initial_train, step=7)
    after = rolling_origin_evaluate(corrupted, horizon=7, initial_train=initial_train, step=7)

    # Compare forecasts only: the actuals legitimately differ between runs.
    columns = ["model", "fold", "origin_date", "date", "forecast"]
    first_before = before[before["fold"] == 1][columns].reset_index(drop=True)
    first_after = after[after["fold"] == 1][columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(first_before, first_after)


def test_rolling_origin_produces_expected_fold_layout() -> None:
    series = make_series([float(v) for v in range(60)])
    predictions = rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7)

    models = predictions["model"].nunique()
    assert predictions["fold"].nunique() == 4
    assert len(predictions) == 4 * 7 * models
    # Every origin must sit strictly before the dates it is scored on.
    assert (predictions["origin_date"] < predictions["date"]).all()


def test_rolling_origin_rejects_series_too_short_for_settings() -> None:
    with pytest.raises(ValueError, match="too short"):
        rolling_origin_evaluate(make_series([float(v) for v in range(20)]), horizon=7, initial_train=28)


def test_summarise_backtest_pools_folds_into_one_row_per_model() -> None:
    series = make_series([float(v) for v in [12, 19, 14, 22, 17, 25, 20] * 8])
    summary = summarise_backtest(rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7))

    assert len(summary) == len(default_model_suite())
    assert summary["model"].is_unique
    # Sorted best-first by the headline metric.
    assert summary["wape"].is_monotonic_increasing


def test_compare_against_baseline_reports_relative_improvement() -> None:
    summary = pd.DataFrame(
        {
            "model": ["naive", "better"],
            "wape": [0.20, 0.15],
        }
    )
    compared = compare_against_baseline(summary, baseline="naive")

    naive_row = compared[compared["model"] == "naive"].iloc[0]
    better_row = compared[compared["model"] == "better"].iloc[0]
    assert naive_row["wape_improvement_vs_baseline"] == 0.0
    assert better_row["wape_improvement_vs_baseline"] == pytest.approx(0.25)


def test_compare_against_baseline_requires_the_baseline_to_exist() -> None:
    summary = pd.DataFrame({"model": ["seasonal_mean_7"], "wape": [0.1]})
    with pytest.raises(ValueError, match="not present"):
        compare_against_baseline(summary, baseline="naive")


def test_evaluate_holdout_scores_every_model_on_the_same_window() -> None:
    series = make_series([float(v) for v in [12, 19, 14, 22, 17, 25, 20] * 8])
    result = evaluate_holdout(series, horizon=14)

    assert len(result) == len(default_model_suite())
    assert set(result["n_observations"]) == {14}


# --------------------------------------------------------------------------
# Integration with the cleaned pipeline output
# --------------------------------------------------------------------------


def test_forecasting_runs_on_cleaned_pipeline_output(cleaned_dataset) -> None:
    tables = cleaned_dataset.tables
    daily = build_daily_series(tables["fact_sales"], tables["fact_orders"])

    assert len(daily) == 60
    assert list(daily.columns) == ["net_sales", "units", "orders"]
    assert daily.index.is_monotonic_increasing
    assert not daily.isna().any().any()
    # Gap-free, so seasonal positions stay aligned to real weekdays.
    assert daily.index.equals(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))


def test_seasonal_model_beats_naive_baseline_on_real_sales(cleaned_dataset) -> None:
    """The weekly model must earn its place against the reference baseline.

    Daily sales carry a real day-of-week profile: lag-7 and lag-14 are the only
    materially positive autocorrelations, while lag-1 is negative. A seasonal
    model should therefore beat carry-the-last-value by a clear margin.

    The threshold is set well below the measured improvement so that ordinary
    noise cannot flip it, while a genuine regression in the seasonal model or a
    loss of weekly structure in the data still fails the test.
    """
    tables = cleaned_dataset.tables
    daily = build_daily_series(tables["fact_sales"], tables["fact_orders"])

    for metric in ("net_sales", "units"):
        series = extract_metric(daily, metric)
        summary = compare_against_baseline(
            summarise_backtest(
                rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7)
            )
        )
        seasonal = summary[summary["model"] == "seasonal_mean_7"].iloc[0]
        naive_row = summary[summary["model"] == "naive"].iloc[0]

        assert seasonal["wape"] < naive_row["wape"], metric
        assert seasonal["wape_improvement_vs_baseline"] > 0.10, metric
        assert summary["wape"].notna().all(), metric


def test_backtest_is_reproducible_across_runs(cleaned_dataset) -> None:
    """Two identical backtests must agree exactly, or the numbers are not evidence."""
    tables = cleaned_dataset.tables
    series = extract_metric(build_daily_series(tables["fact_sales"], tables["fact_orders"]), "net_sales")

    first = summarise_backtest(rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7))
    second = summarise_backtest(rolling_origin_evaluate(series, horizon=7, initial_train=28, step=7))
    pd.testing.assert_frame_equal(first, second)
