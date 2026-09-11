"""Classify what a body of evidence is consistent with.

The wording here is deliberate throughout. This module reports that an observed
pattern is *consistent with* an explanation, names the leading alternative, and
says whether the data supports that alternative too. It does not claim that
anything was caused by anything.

That restraint is not pedantry. Demand and fulfilment moving together is exactly
as consistent with a supply constraint as with a demand shift until you look at
which one moved first and which one moved at all, and the system should say so
rather than pick a story.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FULFILMENT_CONSTRAINT = "fulfilment_constraint"
DEMAND_SOFTNESS = "demand_softness"
PORTFOLIO_MIX_SHIFT = "portfolio_mix_shift"
UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class PatternAssessment:
    """What the evidence is consistent with, and what it is not."""

    pattern: str
    statement: str
    alternative_hypothesis: str
    alternative_supported: bool | None
    alternative_note: str
    recommended_investigation: str
    corroborating: tuple[str, ...] = field(default_factory=tuple)
    contradicting: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "statement": self.statement,
            "alternative_hypothesis": self.alternative_hypothesis,
            "alternative_supported": self.alternative_supported,
            "alternative_note": self.alternative_note,
            "recommended_investigation": self.recommended_investigation,
            "corroborating": list(self.corroborating),
            "contradicting": list(self.contradicting),
        }


def _direction(evidence: dict[str, dict], name: str) -> str | None:
    item = evidence.get(name)
    return None if item is None else item["direction"]


def _pct(evidence: dict[str, dict], name: str) -> str:
    """Render a movement as a percentage for an evidence phrase."""
    item = evidence.get(name)
    if item is None or item.get("change_pct") is None:
        return ""
    return f" ({item['change_pct']:+.1%})"


def _fulfilment_lagged_demand(evidence: dict[str, dict]) -> bool:
    """Did fulfilment fail to keep pace with what was asked of it?

    Shipping more units than last week is not evidence that fulfilment held up.
    A segment whose demand rose 38% while its shipments rose 9% served a far
    smaller share of its orders, and its fill rate says so. Requiring fulfilled
    units to fall in absolute terms would miss exactly that case, which is the
    most common shape a supply constraint takes in a growing segment.
    """
    if _direction(evidence, "fill_rate") == "down":
        return True
    return _direction(evidence, "fulfilled_units") == "down"


def classify(
    evidence: dict[str, dict],
    segment: str,
    dominant_component: str = "contribution",
) -> PatternAssessment:
    """Describe what this combination of movements is consistent with.

    The decisive question is whether demand held while fulfilment fell, or
    whether demand itself weakened. Inventory evidence, where present,
    corroborates the first reading but is not required to reach it.
    """
    demand = _direction(evidence, "ordered_units")
    fulfilled = _direction(evidence, "fulfilled_units")
    stock = _direction(evidence, "available_stock")
    stockouts = _direction(evidence, "stockout_rate")

    corroborating: list[str] = []
    contradicting: list[str] = []

    if dominant_component == "mix_effect":
        return PatternAssessment(
            pattern=PORTFOLIO_MIX_SHIFT,
            statement=(
                f"The movement in {segment} is concentrated in mix rather than in "
                "fulfilment performance: demand moved between segments whose fill "
                "rates already differed."
            ),
            alternative_hypothesis="Fulfilment performance deteriorated within the segments themselves.",
            alternative_supported=fulfilled == "down" and demand != "down",
            alternative_note=(
                "Segment-level fill rates would need to fall for that reading; check "
                "the rate effect before treating this as a supply problem."
            ),
            recommended_investigation=(
                f"Review what changed in the demand blend for {segment}, then confirm "
                "whether the segments receiving that demand are stocked to serve it."
            ),
        )

    demand_held = demand in ("up", "flat")
    if demand_held and _fulfilment_lagged_demand(evidence):
        if fulfilled == "down":
            corroborating.append(f"fulfilled units fell{_pct(evidence, 'fulfilled_units')}")
            shape = "fulfilled units fell while ordered units did not"
        else:
            corroborating.append(
                f"fulfilment grew more slowly than demand{_pct(evidence, 'fulfilled_units')}"
            )
            shape = "fulfilment did not keep pace with the demand placed on it"
        corroborating.append(
            f"ordered units {'rose' if demand == 'up' else 'held'}{_pct(evidence, 'ordered_units')}"
        )
        if stock == "down":
            corroborating.append(f"available stock fell{_pct(evidence, 'available_stock')}")
        if stockouts == "up":
            corroborating.append("stockout rate rose")

        supply_note = (
            "Demand did not weaken over this period, so a demand-led explanation "
            "is not supported by the order volumes."
        )
        return PatternAssessment(
            pattern=FULFILMENT_CONSTRAINT,
            statement=(
                f"The movement in {segment} is consistent with a fulfilment or supply "
                f"constraint: {shape}."
            ),
            alternative_hypothesis="Demand weakened and the fall in fulfilment simply followed it.",
            alternative_supported=False,
            alternative_note=supply_note,
            recommended_investigation=(
                f"Review inventory availability and replenishment for {segment}, "
                "starting with the SKUs carrying the largest unfulfilled volume."
            ),
            corroborating=tuple(corroborating),
            contradicting=tuple(contradicting),
        )

    if demand == "down":
        corroborating.append(f"ordered units fell{_pct(evidence, 'ordered_units')}")
        if fulfilled == "down":
            corroborating.append(f"fulfilled units fell with them{_pct(evidence, 'fulfilled_units')}")
        if stock in ("up", "flat"):
            corroborating.append("stock availability held")

        return PatternAssessment(
            pattern=DEMAND_SOFTNESS,
            statement=(
                f"The movement in {segment} is consistent with weaker demand: ordered "
                "units fell, and fulfilment followed rather than led."
            ),
            alternative_hypothesis=(
                "Supply constrained the segment and customers stopped ordering what "
                "they could not receive."
            ),
            alternative_supported=stock == "down" or stockouts == "up",
            alternative_note=(
                "Suppressed demand can look like weak demand. Check whether stock ran "
                "short before orders fell; if it did, the ordering behaviour may be a "
                "response rather than a cause."
            ),
            recommended_investigation=(
                f"Review order patterns for {segment} by customer and channel, and "
                "confirm stock was available throughout the period."
            ),
            corroborating=tuple(corroborating),
            contradicting=tuple(contradicting),
        )

    return PatternAssessment(
        pattern=UNCLASSIFIED,
        statement=(
            f"The movement in {segment} does not match a recognised demand or "
            "fulfilment pattern from the available evidence."
        ),
        alternative_hypothesis="Ordinary variation in a segment too small or too volatile to read.",
        alternative_supported=None,
        alternative_note=(
            "No supporting metric moved decisively enough to distinguish a demand "
            "story from a supply story."
        ),
        recommended_investigation=(
            f"Examine {segment} at a finer grain, or over a longer window, before "
            "treating this movement as a signal."
        ),
    )
