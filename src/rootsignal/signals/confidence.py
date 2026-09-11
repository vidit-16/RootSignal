"""Assess how much weight a signal's evidence can bear.

Confidence here is a count of named criteria that were met, each recorded with
the number behind it. It is deliberately not a tuned score: a single opaque
number would be impossible to argue with, and the point of this layer is to let
someone disagree with a specific step of the reasoning.

None of these criteria is a statistical test, and confidence is not a
probability. It describes how much of the available corroboration lined up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A movement this many times the segment's own typical period-to-period swing is
# treated as standing out from its normal variation.
MATERIAL_MOVEMENT_RATIO = 1.5

# Moving the same way for at least this many consecutive periods.
PERSISTENCE_PERIODS = 2

# Share of the total gross movement the segment must carry to count as a
# meaningful part of it.
CONCENTRATION_SHARE = 0.15

# Independent supporting metrics that must move consistently with the reading.
CORROBORATION_COUNT = 2

# Below this, a decomposition component is reshuffling rather than a direction.
COMPONENT_COHERENCE = 0.30

HIGH_THRESHOLD = 5
MEDIUM_THRESHOLD = 3


@dataclass(frozen=True)
class ConfidenceCriterion:
    name: str
    met: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "met": self.met, "detail": self.detail}


@dataclass(frozen=True)
class ConfidenceAssessment:
    level: str
    met: int
    total: int
    criteria: tuple[ConfidenceCriterion, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "criteria_met": self.met,
            "criteria_total": self.total,
            "criteria": [criterion.as_dict() for criterion in self.criteria],
        }


def assess_confidence(
    movement: float,
    typical_movement: float | None,
    persistence: int,
    share_of_absolute_movement: float | None,
    corroborating_count: int,
    alternative_supported: bool | None,
    component_coherence: float | None,
) -> ConfidenceAssessment:
    """Evaluate each criterion and report the level alongside the reasoning.

    ``typical_movement`` is the segment's own historical period-to-period
    variation for this metric, so a movement is judged against how much that
    segment normally moves rather than against a global threshold. A volatile
    segment has to move further to earn the same confidence.
    """
    criteria: list[ConfidenceCriterion] = []

    if typical_movement is None or typical_movement <= 0:
        criteria.append(
            ConfidenceCriterion(
                "movement_stands_out",
                False,
                "Not enough history to judge this segment's normal variation.",
            )
        )
    else:
        ratio = abs(movement) / typical_movement
        criteria.append(
            ConfidenceCriterion(
                "movement_stands_out",
                ratio >= MATERIAL_MOVEMENT_RATIO,
                f"Movement is {ratio:.2f}x the segment's typical swing "
                f"(threshold {MATERIAL_MOVEMENT_RATIO}).",
            )
        )

    criteria.append(
        ConfidenceCriterion(
            "movement_persisted",
            persistence >= PERSISTENCE_PERIODS,
            f"Moved the same direction for {persistence} consecutive period(s) "
            f"(threshold {PERSISTENCE_PERIODS}).",
        )
    )

    if share_of_absolute_movement is None:
        criteria.append(
            ConfidenceCriterion(
                "segment_carries_the_movement", False, "Contribution share unavailable."
            )
        )
    else:
        criteria.append(
            ConfidenceCriterion(
                "segment_carries_the_movement",
                share_of_absolute_movement >= CONCENTRATION_SHARE,
                f"Segment carries {share_of_absolute_movement:.1%} of total movement "
                f"(threshold {CONCENTRATION_SHARE:.0%}).",
            )
        )

    criteria.append(
        ConfidenceCriterion(
            "other_metrics_agree",
            corroborating_count >= CORROBORATION_COUNT,
            f"{corroborating_count} supporting observation(s) move consistently "
            f"(threshold {CORROBORATION_COUNT}).",
        )
    )

    if alternative_supported is None:
        criteria.append(
            ConfidenceCriterion(
                "alternative_not_supported",
                False,
                "The evidence does not distinguish between competing explanations.",
            )
        )
    else:
        criteria.append(
            ConfidenceCriterion(
                "alternative_not_supported",
                not alternative_supported,
                "The leading alternative explanation is "
                + ("also supported by the data." if alternative_supported else "not supported by the data."),
            )
        )

    if component_coherence is None:
        criteria.append(
            ConfidenceCriterion(
                "movement_is_directional",
                True,
                "Additive metric; the movement has no components to reshuffle.",
            )
        )
    else:
        criteria.append(
            ConfidenceCriterion(
                "movement_is_directional",
                component_coherence >= COMPONENT_COHERENCE,
                f"Dominant component coherence is {component_coherence:.3f} "
                f"(threshold {COMPONENT_COHERENCE}); below it the effects cancel out.",
            )
        )

    met = sum(1 for criterion in criteria if criterion.met)
    if met >= HIGH_THRESHOLD:
        level = "high"
    elif met >= MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return ConfidenceAssessment(level=level, met=met, total=len(criteria), criteria=tuple(criteria))
