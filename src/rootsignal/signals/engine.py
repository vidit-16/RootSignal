"""Assemble evidence-backed signals worth investigating.

This is the layer the rest of the system exists to support. It takes a metric
movement, finds the segments carrying it, gathers what the surrounding
operational metrics did, estimates the commercial size, judges how much the
evidence supports the reading, and ranks what to look at first.

Every number in a signal is computed here or upstream, deterministically. The
prose fields are assembled from those numbers by fixed templates. Nothing in a
signal is inferred by a language model, and a signal states what its evidence is
consistent with rather than what caused it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis.periods import PERIOD_COLUMN
from ..analysis.trends import summarise_by_period
from ..decomposition.contribution import decompose_movement
from ..decomposition.drivers import component_coherence, dominant_component, rank_drivers
from ..impact.estimation import ImpactEstimate, estimate_fulfilment_shortfall
from .confidence import ConfidenceAssessment, assess_confidence
from .evidence import gather_segment_evidence, periods_of_consistent_movement, summarise_inventory_by_period
from .patterns import PatternAssessment, classify

# Ranking weights. A low-confidence signal is not hidden, only pushed down, so
# that a large but weakly evidenced movement still reaches the reader.
CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.6, "low": 0.3}

# Dimensions that inventory can be grouped by. Inventory has no customer,
# channel or manager, so evidence from it is unavailable at those grains.
INVENTORY_DIMENSIONS = frozenset({"region_code", "sku_id", "warehouse", "category", "sub_category"})


@dataclass(frozen=True)
class RootSignal:
    """A movement, where it happened, what surrounds it, and what to do next."""

    metric: str
    dimension: str
    segment: str
    period_start: pd.Timestamp
    comparison_period: pd.Timestamp
    movement: float
    movement_pct: float | None
    contribution: float
    share_of_absolute_movement: float | None
    ranked_by: str
    pattern: PatternAssessment
    supporting_evidence: dict[str, dict]
    impact: ImpactEstimate | None
    confidence: ConfidenceAssessment
    priority_score: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        """The structured evidence package an explanation layer would consume."""
        return {
            "metric": self.metric,
            "dimension": self.dimension,
            "segment": self.segment,
            "period": str(pd.Timestamp(self.period_start).date()),
            "comparison_period": str(pd.Timestamp(self.comparison_period).date()),
            "movement": round(self.movement, 6),
            "movement_pct": None if self.movement_pct is None else round(self.movement_pct, 4),
            "contribution": round(self.contribution, 6),
            "share_of_absolute_movement": self.share_of_absolute_movement,
            "ranked_by": self.ranked_by,
            "likely_driver": self.pattern.pattern,
            "statement": self.pattern.statement,
            "supporting_evidence": self.supporting_evidence,
            "corroborating": list(self.pattern.corroborating),
            "impact": None
            if self.impact is None
            else {
                "value": self.impact.value,
                "basis": self.impact.basis,
                "components": self.impact.components,
                "assumptions": list(self.impact.assumptions),
            },
            "confidence": self.confidence.as_dict(),
            "alternative_hypothesis": self.pattern.alternative_hypothesis,
            "alternative_supported": self.pattern.alternative_supported,
            "alternative_note": self.pattern.alternative_note,
            "recommended_investigation": self.pattern.recommended_investigation,
            "priority_score": round(self.priority_score, 4),
            "notes": list(self.notes),
        }


def _typical_movement(
    summary: pd.DataFrame,
    metric: str,
    dimension: Sequence[str],
    segment: Sequence[str],
) -> float | None:
    """The segment's own typical period-to-period swing in this metric.

    Judging a movement against the segment's history rather than a global
    threshold means a volatile segment must move further to look notable.
    """
    mask = pd.Series(True, index=summary.index)
    for column, value in zip(list(dimension), list(segment), strict=True):
        mask &= summary[column].astype(str) == str(value)
    rows = summary.loc[mask].sort_values(PERIOD_COLUMN)
    if metric not in rows.columns or len(rows) < 3:
        return None

    changes = rows[metric].astype(float).diff().dropna()
    if changes.empty:
        return None
    typical = float(changes.abs().median())
    return typical if typical > 0 else None


def _segment_values(summary: pd.DataFrame, dimension: Sequence[str], segment: Sequence[str], period) -> pd.Series:
    mask = pd.to_datetime(summary[PERIOD_COLUMN]) == pd.Timestamp(period)
    for column, value in zip(list(dimension), list(segment), strict=True):
        mask &= summary[column].astype(str) == str(value)
    rows = summary.loc[mask]
    return rows.iloc[0] if len(rows) else pd.Series(dtype="float64")


def detect_signals(
    tables: dict[str, pd.DataFrame],
    metric: str = "fill_rate",
    dimension: Sequence[str] = ("region_code", "category"),
    period: str = "week",
    current_period: object | None = None,
    comparison_period: object | None = None,
    top_n: int = 3,
    direction: str = "negative",
    min_confidence: str | None = None,
) -> list[RootSignal]:
    """Find and assemble the signals behind a metric movement.

    Segments are selected by the decomposition component that actually carries
    the movement, not by the size of their total contribution. For a rate, those
    differ: a segment whose fulfilment collapsed can show a positive total
    contribution if demand moved away from it at the same time, and ranking on
    the total would drop it from the list entirely.

    ``min_confidence`` discards signals below a level. Without it the function
    always returns its top segments, so a quiet period yields a ranked list of
    ordinary noise and the engine can never report that nothing happened. Set it
    to "high" for a watchlist that stays silent unless the evidence lines up.
    """
    dims = list(dimension)
    summary = summarise_by_period(tables, period=period, group_by=dims)
    if summary.empty:
        return []

    decomposition = decompose_movement(summary, metric, dims, current_period, comparison_period)
    current = pd.Timestamp(decomposition[PERIOD_COLUMN].iloc[0])
    comparison = pd.Timestamp(decomposition["comparison_period"].iloc[0])

    ranking_column = dominant_component(decomposition)
    coherence = None
    if ranking_column != "contribution":
        components = component_coherence(decomposition)
        coherence = float(components.loc[components["component"] == ranking_column, "coherence"].iloc[0])

    # Concentration is measured within whichever component the ranking used.
    # Measuring a segment's share of total contribution while ranking on the
    # rate effect would divide by a denominator inflated with mix noise, and
    # make every segment look like a negligible part of the movement.
    component_gross = float(decomposition[ranking_column].abs().sum())

    leaders = rank_drivers(decomposition, direction=direction, top_n=top_n, by=ranking_column)
    if leaders.empty:
        return []

    inventory_summary = None
    notes: list[str] = []
    if set(dims) <= INVENTORY_DIMENSIONS:
        inventory_summary = summarise_inventory_by_period(tables, period=period, group_by=dims)
    else:
        notes.append(
            "Inventory evidence is unavailable at this grain; inventory is not held "
            "by customer, channel or manager."
        )

    signals: list[RootSignal] = []
    for _, leader in leaders.iterrows():
        segment_values = [leader[column] for column in dims]
        segment_label = str(leader["segment"])

        evidence = gather_segment_evidence(
            summary, dims, segment_values, current, comparison, inventory_summary
        )
        pattern = classify(evidence, segment_label, ranking_column)

        before = _segment_values(summary, dims, segment_values, comparison)
        after = _segment_values(summary, dims, segment_values, current)
        movement = float(after.get(metric, np.nan)) - float(before.get(metric, np.nan))
        previous_value = float(before.get(metric, np.nan))
        movement_pct = movement / abs(previous_value) if previous_value not in (0, np.nan) else None

        impact = None
        if {"ordered_units", "fulfilled_units", "net_sales"} <= set(after.index):
            baseline_fill = before.get("fill_rate")
            if pd.notna(baseline_fill):
                impact = estimate_fulfilment_shortfall(
                    ordered_units=float(after["ordered_units"]),
                    fulfilled_units=float(after["fulfilled_units"]),
                    baseline_fill_rate=float(min(max(baseline_fill, 0.0), 1.0)),
                    net_sales=float(after["net_sales"]),
                )

        component_share = (
            abs(float(leader[ranking_column])) / component_gross if component_gross > 0 else None
        )
        confidence = assess_confidence(
            movement=movement,
            typical_movement=_typical_movement(summary, metric, dims, segment_values),
            persistence=periods_of_consistent_movement(summary, metric, dims, segment_values, current),
            share_of_absolute_movement=component_share,
            corroborating_count=len(pattern.corroborating),
            alternative_supported=pattern.alternative_supported,
            component_coherence=coherence,
        )

        impact_value = abs(impact.value) if impact is not None else 0.0
        priority = impact_value * CONFIDENCE_WEIGHTS[confidence.level]

        signals.append(
            RootSignal(
                metric=metric,
                dimension=" | ".join(dims),
                segment=segment_label,
                period_start=current,
                comparison_period=comparison,
                movement=movement,
                movement_pct=movement_pct,
                contribution=float(leader["contribution"]),
                share_of_absolute_movement=leader.get("share_of_absolute_movement"),
                ranked_by=ranking_column,
                pattern=pattern,
                supporting_evidence=evidence,
                impact=impact,
                confidence=confidence,
                priority_score=priority,
                notes=tuple(notes),
            )
        )

    if min_confidence is not None:
        if min_confidence not in CONFIDENCE_WEIGHTS:
            raise ValueError(
                f"min_confidence must be one of {sorted(CONFIDENCE_WEIGHTS)}; got {min_confidence!r}."
            )
        floor = CONFIDENCE_WEIGHTS[min_confidence]
        signals = [
            signal
            for signal in signals
            if CONFIDENCE_WEIGHTS[signal.confidence.level] >= floor
        ]

    return sorted(signals, key=lambda signal: signal.priority_score, reverse=True)


def signals_to_frame(signals: Sequence[RootSignal]) -> pd.DataFrame:
    """Flatten signals into a table for reporting and dashboards."""
    if not signals:
        return pd.DataFrame(
            columns=[
                "metric", "segment", "period", "movement", "likely_driver",
                "impact", "confidence", "priority_score", "recommended_investigation",
            ]
        )
    return pd.DataFrame(
        [
            {
                "metric": signal.metric,
                "segment": signal.segment,
                "period": pd.Timestamp(signal.period_start).date(),
                "movement": round(signal.movement, 4),
                "likely_driver": signal.pattern.pattern,
                "impact": None if signal.impact is None else signal.impact.value,
                "confidence": signal.confidence.level,
                "priority_score": round(signal.priority_score, 2),
                "recommended_investigation": signal.pattern.recommended_investigation,
            }
            for signal in signals
        ]
    )


def explain_signal(signal: RootSignal) -> str:
    """Render a signal as plain text, assembled only from its own numbers.

    This is a deterministic template, not a generated explanation. An optional
    language-model layer would take ``signal.as_dict()`` and write more fluently,
    but it would still be restating these figures rather than producing them.
    """
    lines = [
        f"{signal.metric} in {signal.segment} moved {signal.movement:+.4f}"
        + (f" ({signal.movement_pct:+.1%})" if signal.movement_pct is not None else "")
        + f" for the {signal.period_start.date()} period versus {signal.comparison_period.date()}.",
        signal.pattern.statement,
    ]
    if signal.pattern.corroborating:
        lines.append("Supporting evidence: " + "; ".join(signal.pattern.corroborating) + ".")
    if signal.impact is not None and signal.impact.value > 0:
        lines.append(f"Estimated impact: {signal.impact.describe()}.")
    lines.append(
        f"Confidence: {signal.confidence.level} "
        f"({signal.confidence.met} of {signal.confidence.total} criteria met)."
    )
    lines.append(f"Alternative considered: {signal.pattern.alternative_hypothesis} {signal.pattern.alternative_note}")
    lines.append(f"Recommended investigation: {signal.pattern.recommended_investigation}")
    return " ".join(lines)
