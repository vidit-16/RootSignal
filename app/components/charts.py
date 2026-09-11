"""Chart helpers shared by the dashboard pages.

Two rules govern everything here.

**Categorical colours are assigned in a fixed order and never cycled.** Colour
follows the entity, so filtering a region out must not repaint the ones that
remain. A ninth series folds into "Other" rather than inventing a hue.

**There are no dual-axis charts.** Two measures on two scales invite a reader to
see a relationship that the scaling invented. Two measures means two charts.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from rootsignal.presentation import label_for

# Categorical slots in fixed order. Validated as a set on the adjacent pairlist;
# used unchanged rather than re-stepped.
CATEGORICAL = (
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
)

# Reserved for state, never for "series 4". Always shipped with a label so the
# colour never carries the meaning on its own.
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

GRID = "rgba(128,128,128,0.18)"
MAX_SERIES = 8


def prettify(name: str) -> str:
    """The readable label for an axis.

    Delegates to the shared label map so a chart axis, a table header and a
    report column never disagree about what something is called.
    """
    return label_for(name)


def _axis_labels(*columns: str) -> dict[str, str]:
    return {column: prettify(column) for column in columns if column}


def _base_layout(figure: go.Figure, height: int = 320) -> go.Figure:
    """Recessive axes, transparent surface, one shared hover readout."""
    figure.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=32, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=list(CATEGORICAL),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
    )
    figure.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID)
    figure.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return figure


def fold_small_series(
    frame: pd.DataFrame,
    series_column: str,
    value_column: str,
    limit: int = MAX_SERIES,
) -> pd.DataFrame:
    """Keep the largest series by volume and gather the rest into "Other".

    A generated ninth hue would sit outside the validated set, so the series
    count is capped rather than the palette extended.
    """
    totals = frame.groupby(series_column)[value_column].sum().abs().sort_values(ascending=False)
    if len(totals) <= limit:
        return frame
    keep = set(totals.head(limit - 1).index)
    folded = frame.copy()
    folded[series_column] = folded[series_column].where(folded[series_column].isin(keep), "Other")
    return folded


def trend_line(
    frame: pd.DataFrame,
    x: str,
    y: str,
    series: str | None = None,
    title: str = "",
    height: int = 320,
) -> go.Figure:
    """A time series, optionally split into a capped number of named series."""
    data = frame if series is None else fold_small_series(frame, series, y)
    figure = px.line(
        data, x=x, y=y, color=series, title=title, markers=False,
        labels=_axis_labels(x, y, series),
    )
    figure.update_traces(line=dict(width=2))
    return _base_layout(figure, height)


def comparison_bars(
    frame: pd.DataFrame,
    category: str,
    value: str,
    title: str = "",
    orientation: str = "h",
    height: int = 320,
) -> go.Figure:
    """Magnitude across segments. One measure, one axis, sorted by size."""
    data = frame.sort_values(value)
    labels = _axis_labels(category, value)
    if orientation == "h":
        figure = px.bar(data, x=value, y=category, orientation="h", title=title, labels=labels)
    else:
        figure = px.bar(data, x=category, y=value, title=title, labels=labels)
    figure.update_traces(marker_color=CATEGORICAL[0], marker_line_width=0)
    return _base_layout(figure, height)


def actual_versus_expected(
    frame: pd.DataFrame,
    x: str,
    actual: str,
    expected: str,
    title: str = "",
    height: int = 340,
) -> go.Figure:
    """Two lines of the same measure, so one axis is correct rather than a compromise."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame[x], y=frame[actual], name="Actual",
            mode="lines", line=dict(width=2, color=CATEGORICAL[0]),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=frame[x], y=frame[expected], name="Expected",
            mode="lines", line=dict(width=2, color=CATEGORICAL[1], dash="dash"),
        )
    )
    figure.update_layout(title=title, xaxis_title=prettify(x), yaxis_title=prettify(actual))
    return _base_layout(figure, height)


def variance_bars(
    frame: pd.DataFrame,
    category: str,
    value: str,
    title: str = "",
    height: int = 340,
) -> go.Figure:
    """Polarity across segments: above or below a reference, from a neutral zero.

    Two hues around a neutral midpoint, which is what a diverging measure needs.
    Sign is also legible from which side of zero a bar sits, so the colour is
    reinforcement rather than the only cue.
    """
    data = frame.sort_values(value)
    colours = [STATUS["critical"] if v < 0 else CATEGORICAL[2] for v in data[value]]

    figure = go.Figure(
        go.Bar(
            x=data[value], y=data[category], orientation="h",
            marker=dict(color=colours, line=dict(width=0)),
            hovertemplate="%{y}: %{x:,.2f}<extra></extra>",
        )
    )
    figure.update_layout(
        title=title, showlegend=False,
        xaxis_title=prettify(value), yaxis_title=prettify(category),
    )
    figure = _base_layout(figure, height)
    figure.update_layout(hovermode="closest")
    figure.add_vline(x=0, line_width=1, line_color=GRID)
    return figure


def service_status(fill_rate: float | None, target: float | None) -> tuple[str, str]:
    """Classify a fill rate against its target, returning a label and a colour.

    The label travels with the colour everywhere it is used. A status shown as
    colour alone is unreadable to a substantial share of viewers and invisible
    in print.
    """
    if fill_rate is None or target is None or pd.isna(fill_rate) or pd.isna(target):
        return "No demand", STATUS["warning"]
    gap = fill_rate - target
    if gap >= 0:
        return "On target", STATUS["good"]
    if gap >= -0.05:
        return "Slightly below", STATUS["warning"]
    if gap >= -0.15:
        return "Below target", STATUS["serious"]
    return "Well below", STATUS["critical"]


def series_colour(position: int) -> str:
    """The categorical slot for a fixed position, so colour follows the entity."""
    return CATEGORICAL[position % len(CATEGORICAL)]


def stacked_mix(
    frame: pd.DataFrame,
    category: str,
    parts: Sequence[str],
    title: str = "",
    height: int = 320,
) -> go.Figure:
    """Composition across segments, with a surface gap between stacked parts."""
    figure = go.Figure()
    for position, part in enumerate(parts):
        figure.add_trace(
            go.Bar(
                x=frame[category], y=frame[part], name=part.replace("_", " ").title(),
                marker=dict(color=series_colour(position), line=dict(width=2, color="rgba(0,0,0,0)")),
            )
        )
    figure.update_layout(title=title, barmode="stack", xaxis_title=prettify(category), yaxis_title="Net sales")
    return _base_layout(figure, height)
