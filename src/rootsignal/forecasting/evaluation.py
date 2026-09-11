"""Forecast accuracy metrics and backtesting.

Accuracy is measured out-of-sample only. A model is refitted at every origin
using data strictly before the window it is scored on, so no evaluation number
depends on an observation the model had already seen.

WAPE is the headline metric. It aggregates error against total actual volume,
so it stays interpretable when individual days are small, and it cannot be
distorted by a single near-zero denominator the way MAPE can.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from .models import Forecaster, default_model_suite

# Below this share of mean actual volume, a percentage error stops being
# meaningful and MAPE is reported as undefined rather than as a large number.
MAPE_FLOOR_RATIO = 0.01


def mae(actual: pd.Series, forecast: pd.Series) -> float:
    """Mean absolute error, in the units of the metric."""
    return float(np.mean(np.abs(actual.to_numpy() - forecast.to_numpy())))


def rmse(actual: pd.Series, forecast: pd.Series) -> float:
    """Root mean squared error; penalises large misses more than MAE."""
    return float(np.sqrt(np.mean((actual.to_numpy() - forecast.to_numpy()) ** 2)))


def wape(actual: pd.Series, forecast: pd.Series) -> float:
    """Weighted absolute percentage error: total error over total actual volume."""
    actual_values = actual.to_numpy()
    denominator = float(np.sum(np.abs(actual_values)))
    if denominator == 0.0:
        return float("nan")
    return float(np.sum(np.abs(actual_values - forecast.to_numpy())) / denominator)


def mape(actual: pd.Series, forecast: pd.Series) -> float:
    """Mean absolute percentage error, or NaN when it would be misleading.

    MAPE divides by each actual in turn, so a single near-zero day can dominate
    the average. When any actual falls below a small fraction of mean volume the
    metric is reported as undefined instead of as a spuriously large number.
    """
    actual_values = actual.to_numpy()
    scale = float(np.mean(np.abs(actual_values)))
    if scale == 0.0:
        return float("nan")
    if float(np.min(np.abs(actual_values))) < MAPE_FLOOR_RATIO * scale:
        return float("nan")
    return float(np.mean(np.abs((actual_values - forecast.to_numpy()) / actual_values)))


def bias(actual: pd.Series, forecast: pd.Series) -> float:
    """Mean signed error. Positive means the forecast ran high."""
    return float(np.mean(forecast.to_numpy() - actual.to_numpy()))


def score_forecast(actual: pd.Series, forecast: pd.Series) -> dict[str, float]:
    """Score one aligned actual/forecast pair across every metric."""
    if len(actual) != len(forecast):
        raise ValueError(
            f"Actual and forecast lengths differ: {len(actual)} vs {len(forecast)}."
        )
    if not actual.index.equals(forecast.index):
        raise ValueError("Actual and forecast must share the same dates.")
    return {
        "mae": mae(actual, forecast),
        "rmse": rmse(actual, forecast),
        "wape": wape(actual, forecast),
        "mape": mape(actual, forecast),
        "bias": bias(actual, forecast),
    }


def holdout_split(series: pd.Series, horizon: int) -> tuple[pd.Series, pd.Series]:
    """Split a series into training history and a final holdout window."""
    if horizon < 1:
        raise ValueError("Horizon must be at least 1 day.")
    if horizon >= len(series):
        raise ValueError(
            f"Horizon {horizon} leaves no training history in a series of {len(series)} days."
        )
    return series.iloc[:-horizon], series.iloc[-horizon:]


def evaluate_holdout(
    series: pd.Series,
    models: Sequence[Forecaster] | None = None,
    horizon: int = 14,
) -> pd.DataFrame:
    """Score every model on a single final holdout window."""
    train, test = holdout_split(series, horizon)
    suite = list(models or default_model_suite())

    rows = []
    for model in suite:
        forecast = model.fit_predict(train, horizon)
        forecast.index = test.index
        rows.append({"model": model.name, "n_observations": len(test), **score_forecast(test, forecast)})
    return pd.DataFrame(rows).sort_values("wape", ignore_index=True)


def rolling_origin_evaluate(
    series: pd.Series,
    models: Sequence[Forecaster] | None = None,
    horizon: int = 7,
    initial_train: int = 28,
    step: int = 7,
) -> pd.DataFrame:
    """Backtest across successive origins with an expanding training window.

    A single holdout on a short series scores every model on one accident of
    timing. Re-forecasting from several origins and pooling the errors gives a
    far more stable comparison, which matters here because the history is only
    sixty days long.

    Errors are pooled across folds rather than averaged per fold, so a fold is
    weighted by the volume it actually carries.
    """
    if initial_train < 1:
        raise ValueError("initial_train must be at least 1 day.")
    if step < 1:
        raise ValueError("step must be at least 1 day.")
    if initial_train + horizon > len(series):
        raise ValueError(
            f"Series of {len(series)} days is too short for initial_train="
            f"{initial_train} plus horizon={horizon}."
        )

    suite = list(models or default_model_suite())
    origins = range(initial_train, len(series) - horizon + 1, step)

    records = []
    for fold, origin in enumerate(origins, start=1):
        train = series.iloc[:origin]
        test = series.iloc[origin : origin + horizon]
        for model in suite:
            forecast = model.fit_predict(train, horizon)
            forecast.index = test.index
            records.append(
                pd.DataFrame(
                    {
                        "model": model.name,
                        "fold": fold,
                        "origin_date": train.index.max(),
                        "date": test.index,
                        "actual": test.to_numpy(),
                        "forecast": forecast.to_numpy(),
                    }
                )
            )

    if not records:
        raise ValueError("Rolling-origin evaluation produced no folds; widen the series or reduce step.")
    return pd.concat(records, ignore_index=True)


def summarise_backtest(predictions: pd.DataFrame) -> pd.DataFrame:
    """Pool rolling-origin predictions into one accuracy row per model."""
    required = {"model", "fold", "actual", "forecast"}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"Backtest predictions are missing columns: {sorted(missing)}")

    rows = []
    for name, group in predictions.groupby("model", sort=False):
        actual = group["actual"].reset_index(drop=True)
        forecast = group["forecast"].reset_index(drop=True)
        rows.append(
            {
                "model": name,
                "folds": int(group["fold"].nunique()),
                "n_observations": len(group),
                **score_forecast(actual, forecast),
            }
        )
    return pd.DataFrame(rows).sort_values("wape", ignore_index=True)


def compare_against_baseline(summary: pd.DataFrame, baseline: str = "naive") -> pd.DataFrame:
    """Express each model's WAPE as an improvement over a named baseline.

    The improvement is reported as a share of the baseline's WAPE. It describes
    accuracy on this history and horizon only; it is not a claim about future
    performance on unseen data.
    """
    if baseline not in set(summary["model"]):
        raise ValueError(f"Baseline '{baseline}' is not present in the summary.")

    baseline_wape = float(summary.loc[summary["model"] == baseline, "wape"].iloc[0])
    result = summary.copy()
    if baseline_wape == 0.0:
        result["wape_improvement_vs_baseline"] = np.nan
    else:
        result["wape_improvement_vs_baseline"] = (
            (baseline_wape - result["wape"]) / baseline_wape
        ).round(4)
    result["baseline"] = baseline
    return result
