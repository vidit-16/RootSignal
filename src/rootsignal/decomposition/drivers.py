"""Rank and compare the segments behind a metric movement.

Contributions are calculated first and ranked afterwards. Ranking a metric level
rather than a contribution would simply surface the largest segments, which is
not the same question: a big segment that barely moved explains nothing.

Nothing in this module establishes cause. A segment that accounts for most of a
movement is where the movement happened, not why it happened.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from ..analysis.periods import PERIOD_COLUMN
from .contribution import decompose_movement


RATE_EFFECT_COLUMNS = ("rate_effect", "mix_effect", "interaction_effect")


def rank_drivers(
    decomposition: pd.DataFrame,
    direction: str = "negative",
    top_n: int = 5,
    by: str = "contribution",
) -> pd.DataFrame:
    """Order segments by how much of the movement they account for.

    ``by`` selects which column to rank on. For a rate decomposition this
    matters a great deal: a segment whose fulfilment genuinely collapsed can
    still show a positive total contribution if demand moved away from it at the
    same time. Ranking such a movement by total contribution reports the segment
    as a positive contributor and hides the deterioration completely. Use
    ``dominant_component`` to choose the column rather than guessing.
    """
    if direction not in {"negative", "positive", "absolute"}:
        raise ValueError("direction must be 'negative', 'positive' or 'absolute'.")
    if decomposition.empty:
        return decomposition
    if by not in decomposition.columns:
        raise ValueError(f"Decomposition has no column '{by}' to rank by.")

    frame = decomposition.copy()
    if direction == "negative":
        frame = frame[frame[by] < 0].sort_values(by)
    elif direction == "positive":
        frame = frame[frame[by] > 0].sort_values(by, ascending=False)
    else:
        frame = frame.reindex(frame[by].abs().sort_values(ascending=False).index)

    frame = frame.head(top_n).reset_index(drop=True)
    frame.insert(0, "rank", range(1, len(frame) + 1))
    frame.insert(1, "ranked_by", by)
    return frame


def component_coherence(decomposition: pd.DataFrame) -> pd.DataFrame:
    """Report, per component of a rate decomposition, whether it moved as one.

    ``coherence`` is net movement divided by gross movement. A component whose
    segment effects are individually large but cancel almost exactly has a
    coherence near zero: demand reshuffled between segments without the total
    going anywhere. A component with high coherence moved in one direction
    across the business and is the one worth investigating.

    This is a description of how a component behaved, not a significance test.
    """
    available = [column for column in RATE_EFFECT_COLUMNS if column in decomposition.columns]
    if not available:
        raise ValueError("Coherence applies to rate decompositions, which carry effect columns.")

    rows = []
    for component in available:
        net = float(decomposition[component].sum())
        gross = float(decomposition[component].abs().sum())
        rows.append(
            {
                "component": component,
                "net": round(net, 6),
                "gross": round(gross, 6),
                "coherence": round(abs(net) / gross, 4) if gross > 0 else float("nan"),
            }
        )
    frame = pd.DataFrame(rows)
    total_net = float(frame["net"].abs().sum())
    frame["share_of_net_movement"] = (
        (frame["net"].abs() / total_net).round(4) if total_net > 0 else float("nan")
    )
    return frame.reindex(frame["net"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def dominant_component(decomposition: pd.DataFrame) -> str:
    """Name the component carrying most of a rate movement.

    Returns ``contribution`` for an additive decomposition, which has no
    components to separate.
    """
    if not any(column in decomposition.columns for column in RATE_EFFECT_COLUMNS):
        return "contribution"
    return str(component_coherence(decomposition).iloc[0]["component"])


def concentration(decomposition: pd.DataFrame, top_n: int = 3) -> float:
    """Share of the gross movement carried by the largest few segments.

    A high value means the movement is localised and worth investigating in one
    place. A low value means it is spread thinly, which usually points at
    something systemic rather than at any one segment.
    """
    if decomposition.empty:
        return float("nan")
    magnitudes = decomposition["contribution"].abs().sort_values(ascending=False)
    total = float(magnitudes.sum())
    if total == 0.0:
        return float("nan")
    return round(float(magnitudes.head(top_n).sum() / total), 4)


def compare_dimensions(
    summary_by_dimension: dict[str, pd.DataFrame],
    metric: str,
    dimensions: Sequence[Sequence[str]],
    current_period: object | None = None,
    comparison_period: object | None = None,
    top_n: int = 3,
) -> pd.DataFrame:
    """Ask which way of cutting the business localises a movement best.

    Each dimension gives an independent, complete decomposition of the same
    movement. **They must never be added together**: region and category each
    already explain 100% of it, from different angles. This function reports
    which cut concentrates the movement most, as a guide to where to look first.

    ``summary_by_dimension`` maps a dimension key to a period summary already
    aggregated at that grain, because a summary must be built at the grain it is
    analysed at rather than re-aggregated from a finer one.
    """
    rows = []
    for dimension in dimensions:
        key = " | ".join(dimension)
        summary = summary_by_dimension.get(key)
        if summary is None:
            raise ValueError(f"No period summary supplied for dimension '{key}'.")

        decomposition = decompose_movement(
            summary, metric, dimension, current_period, comparison_period
        )
        leader = decomposition.reindex(
            decomposition["contribution"].abs().sort_values(ascending=False).index
        ).iloc[0]
        rows.append(
            {
                "dimension": key,
                "segments": len(decomposition),
                "total_movement": round(float(decomposition["contribution"].sum()), 4),
                "top_segment": leader["segment"],
                "top_contribution": round(float(leader["contribution"]), 4),
                "top_share_of_absolute_movement": leader["share_of_absolute_movement"],
                f"top_{top_n}_concentration": concentration(decomposition, top_n),
            }
        )

    return pd.DataFrame(rows).sort_values(
        f"top_{top_n}_concentration", ascending=False, ignore_index=True
    )


def explain_movement(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    current_period: object | None = None,
    comparison_period: object | None = None,
    top_n: int = 3,
) -> dict:
    """Summarise a movement and the segments that account for most of it.

    Returns plain values rather than prose. Any wording built from this belongs
    upstream of the numbers, and should describe the leading segments as where
    the movement is concentrated rather than as its cause.
    """
    decomposition = decompose_movement(
        summary, metric, dimension, current_period, comparison_period
    )
    movement = float(decomposition["contribution"].sum())
    direction = "negative" if movement < 0 else "positive"

    # Rank on whichever component actually carries the movement. For a rate,
    # ranking on the total would let a segment whose fulfilment collapsed hide
    # behind a simultaneous shift in demand away from it.
    ranking_column = dominant_component(decomposition)
    component_direction = (
        "negative" if float(decomposition[ranking_column].sum()) < 0 else "positive"
    )
    leaders = rank_drivers(
        decomposition, direction=component_direction, top_n=top_n, by=ranking_column
    )

    reported = ["segment", "contribution", "contribution_share", "share_of_absolute_movement"]
    if ranking_column != "contribution":
        reported.insert(1, ranking_column)

    explanation = {
        "metric": metric,
        "dimension": " | ".join(dimension),
        "period": decomposition[PERIOD_COLUMN].iloc[0],
        "comparison_period": decomposition["comparison_period"].iloc[0],
        "total_movement": round(movement, 6),
        "direction": direction,
        "segments": len(decomposition),
        "ranked_by": ranking_column,
        "top_concentration": concentration(decomposition, top_n),
        "leading_segments": leaders[reported].to_dict("records"),
    }
    if ranking_column != "contribution":
        explanation["components"] = component_coherence(decomposition).to_dict("records")
    return explanation
