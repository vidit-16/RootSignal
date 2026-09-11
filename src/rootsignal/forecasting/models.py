"""Deterministic baseline forecasters for daily business metrics.

Every model here is closed-form: no random initialisation, no numerical
optimiser, no dependency on a solver's version. Running a model twice on the
same history returns the same forecast, which is what lets forecast output be
treated as evidence rather than as a suggestion.

Smoothing parameters are chosen by a fixed grid search over the training
history only. A parameter picked on the evaluation window would leak the
answer into the forecast and inflate every accuracy number downstream.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SEASON_LENGTH = 7
ALPHA_GRID = tuple(round(float(a), 2) for a in np.arange(0.05, 1.0, 0.05))


def _validate_history(y: pd.Series) -> pd.Series:
    """Reject histories that would silently corrupt a seasonal forecast."""
    if not isinstance(y.index, pd.DatetimeIndex):
        raise ValueError("Forecast history must be indexed by date.")
    if y.empty:
        raise ValueError("Cannot fit a forecaster on an empty history.")
    if y.isna().any():
        raise ValueError("Forecast history contains missing values.")
    expected = pd.date_range(y.index.min(), y.index.max(), freq="D")
    if len(expected) != len(y.index) or not y.index.equals(expected):
        raise ValueError(
            "Forecast history must be a gap-free daily series; "
            "reindex before fitting so seasonal positions stay aligned."
        )
    return y.astype(float)


def _future_index(history: pd.Series, horizon: int) -> pd.DatetimeIndex:
    if horizon < 1:
        raise ValueError("Forecast horizon must be at least 1 day.")
    start = history.index.max() + pd.Timedelta(days=1)
    return pd.date_range(start, periods=horizon, freq="D")


class Forecaster:
    """Common interface: fit on history, predict a fixed horizon forward."""

    name = "forecaster"

    def fit(self, y: pd.Series) -> Forecaster:
        raise NotImplementedError

    def predict(self, horizon: int) -> pd.Series:
        raise NotImplementedError

    def fit_predict(self, y: pd.Series, horizon: int) -> pd.Series:
        return self.fit(y).predict(horizon)

    def _finalise(self, values: np.ndarray, horizon: int) -> pd.Series:
        index = _future_index(self._history, horizon)
        return pd.Series(np.asarray(values, dtype=float), index=index, name=self.name)


class NaiveForecaster(Forecaster):
    """Carry the last observed value forward.

    This is the reference baseline every other model is measured against. On a
    mean-reverting series it is deliberately weak, which is the point: it shows
    how much structure the other models are actually capturing.
    """

    name = "naive"

    def fit(self, y: pd.Series) -> NaiveForecaster:
        history = _validate_history(y)
        self._level = float(history.iloc[-1])
        self._history = history
        return self

    def predict(self, horizon: int) -> pd.Series:
        return self._finalise(np.repeat(self._level, horizon), horizon)


class SeasonalNaiveForecaster(Forecaster):
    """Repeat the most recent complete season.

    Justified by the data rather than by habit: lag-7 and lag-14 are the only
    materially positive autocorrelations in daily sales, while lag-1 is
    negative.
    """

    def __init__(self, season_length: int = DEFAULT_SEASON_LENGTH) -> None:
        if season_length < 2:
            raise ValueError("season_length must be at least 2.")
        self.season_length = season_length
        self.name = f"seasonal_naive_{season_length}"

    def fit(self, y: pd.Series) -> SeasonalNaiveForecaster:
        history = _validate_history(y)
        if len(history) < self.season_length:
            raise ValueError(
                f"Need at least {self.season_length} observations for "
                f"{self.name}; got {len(history)}."
            )
        self._season = history.iloc[-self.season_length :].to_numpy()
        self._history = history
        return self

    def predict(self, horizon: int) -> pd.Series:
        positions = np.arange(horizon) % self.season_length
        return self._finalise(self._season[positions], horizon)


class SeasonalMeanForecaster(Forecaster):
    """Average each seasonal position across the whole history.

    Uses more of the history than seasonal naive, so it is steadier when one
    recent week was unusual, at the cost of adapting more slowly.
    """

    def __init__(self, season_length: int = DEFAULT_SEASON_LENGTH) -> None:
        if season_length < 2:
            raise ValueError("season_length must be at least 2.")
        self.season_length = season_length
        self.name = f"seasonal_mean_{season_length}"

    def fit(self, y: pd.Series) -> SeasonalMeanForecaster:
        history = _validate_history(y)
        if len(history) < self.season_length:
            raise ValueError(
                f"Need at least {self.season_length} observations for "
                f"{self.name}; got {len(history)}."
            )
        values = history.to_numpy()
        # Seasonal position is measured back from the end of history, so the
        # first forecast step is always position 0 whatever the start date.
        offsets = (np.arange(len(values)) - len(values)) % self.season_length
        self._profile = np.array(
            [values[offsets == position].mean() for position in range(self.season_length)]
        )
        self._history = history
        return self

    def predict(self, horizon: int) -> pd.Series:
        positions = np.arange(horizon) % self.season_length
        return self._finalise(self._profile[positions], horizon)


class MovingAverageForecaster(Forecaster):
    """Project the mean of the most recent window of observations."""

    def __init__(self, window: int = DEFAULT_SEASON_LENGTH) -> None:
        if window < 1:
            raise ValueError("window must be at least 1.")
        self.window = window
        self.name = f"moving_average_{window}"

    def fit(self, y: pd.Series) -> MovingAverageForecaster:
        history = _validate_history(y)
        if len(history) < self.window:
            raise ValueError(
                f"Need at least {self.window} observations for {self.name}; got {len(history)}."
            )
        self._level = float(history.iloc[-self.window :].mean())
        self._history = history
        return self

    def predict(self, horizon: int) -> pd.Series:
        return self._finalise(np.repeat(self._level, horizon), horizon)


def _ses_run(values: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    """Run simple exponential smoothing.

    Returns the one-step-ahead prediction for every position and the final
    smoothed level that the forecast projects forward.
    """
    level = float(values[0])
    predictions = np.empty(len(values), dtype=float)
    predictions[0] = level
    for position in range(1, len(values)):
        predictions[position] = level
        level = alpha * float(values[position]) + (1.0 - alpha) * level
    return predictions, level


class SimpleExponentialSmoothingForecaster(Forecaster):
    """Exponentially weighted level, projected flat.

    When alpha is not supplied it is selected by grid search minimising
    in-sample one-step-ahead squared error on the training history. The grid is
    fixed, so selection is reproducible, and it never sees the evaluation
    window.
    """

    def __init__(self, alpha: float | None = None) -> None:
        if alpha is not None and not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must lie in (0, 1].")
        self.alpha = alpha
        self.name = "ses" if alpha is None else f"ses_{alpha}"

    def fit(self, y: pd.Series) -> SimpleExponentialSmoothingForecaster:
        history = _validate_history(y)
        values = history.to_numpy()

        if self.alpha is not None:
            candidates: tuple[float, ...] = (self.alpha,)
        elif len(values) < 3:
            candidates = (0.5,)
        else:
            candidates = ALPHA_GRID

        best_alpha = candidates[0]
        best_error = np.inf
        for candidate in candidates:
            predictions, _ = _ses_run(values, candidate)
            # Position 0 is seeded from the observation itself, so it carries
            # no information about fit quality.
            error = float(np.sum((values[1:] - predictions[1:]) ** 2))
            if error < best_error:
                best_alpha, best_error = candidate, error

        self.selected_alpha = float(best_alpha)
        _, self._level = _ses_run(values, self.selected_alpha)
        self._history = history
        return self

    def predict(self, horizon: int) -> pd.Series:
        return self._finalise(np.repeat(self._level, horizon), horizon)


def default_model_suite(season_length: int = DEFAULT_SEASON_LENGTH) -> list[Forecaster]:
    """The comparison set used for evaluation reports.

    naive is the required reference point. moving_average is included as a
    second, stronger baseline so that an improvement is not credited purely to
    beating the weakest possible model.
    """
    return [
        NaiveForecaster(),
        MovingAverageForecaster(window=season_length),
        SimpleExponentialSmoothingForecaster(),
        SeasonalNaiveForecaster(season_length=season_length),
        SeasonalMeanForecaster(season_length=season_length),
    ]
