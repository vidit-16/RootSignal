"""Write a signal up as a briefing, using nothing but the signal.

This narrator is deterministic and needs no API key, no network and no
dependencies beyond what the project already installs. It is the default, not a
fallback: everything the system knows about a movement is already in the
evidence package, so composing that into readable English needs no model.

An optional language-model narrator can rephrase this more fluently. It cannot
add anything, because there is nothing to add — it works from the same package
and is checked against it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..presentation import (
    build_name_lookup,
    describe_criterion,
    explain_pattern,
    humanise_segment,
    humanise_statement,
    label_for,
)


@dataclass(frozen=True)
class Briefing:
    """A signal written up in sections, so a caller can use part or all of it."""

    headline: str
    evidence: str
    reading: str
    impact: str
    confidence: str
    recommendation: str
    caveats: tuple[str, ...]

    def as_text(self) -> str:
        """The whole briefing as plain prose."""
        parts = [
            self.headline,
            self.evidence,
            self.reading,
            self.impact,
            self.confidence,
            self.recommendation,
        ]
        return "\n\n".join(part for part in parts if part)

    def as_markdown(self) -> str:
        """The briefing with headings, for a report or a page."""
        sections = [
            ("", self.headline),
            ("What the numbers show", self.evidence),
            ("What that points to", self.reading),
            ("What it is worth", self.impact),
            ("How far the evidence goes", self.confidence),
            ("What to check next", self.recommendation),
        ]
        rendered = []
        for heading, body in sections:
            if not body:
                continue
            rendered.append(f"**{heading}**\n\n{body}" if heading else body)
        if self.caveats:
            rendered.append(
                "**Worth remembering**\n\n"
                + "\n".join(f"- {caveat}" for caveat in self.caveats)
            )
        return "\n\n".join(rendered)

    def as_dict(self) -> dict:
        return {
            "headline": self.headline,
            "evidence": self.evidence,
            "reading": self.reading,
            "impact": self.impact,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "caveats": list(self.caveats),
        }


def _format_movement(metric: str, movement: float, movement_pct: float | None) -> str:
    """Render a movement the way the metric is normally read."""
    if metric in {"fill_rate", "cancellation_rate"}:
        points = movement * 100
        rendered = f"{points:+.1f} percentage points"
    elif abs(movement) < 1:
        rendered = f"{movement:+.4f}"
    else:
        rendered = f"{movement:+,.0f}"
    if movement_pct is not None:
        rendered += f" ({movement_pct:+.1%})"
    return rendered


def _evidence_sentence(package: dict) -> str:
    """Describe the supporting metrics in the order that carries the argument.

    The metric being analysed is left out: the headline has already stated its
    movement, and repeating it as its own supporting evidence would pad the
    sentence without adding anything.
    """
    evidence = package.get("supporting_evidence", {})
    if not evidence:
        return ""
    analysed = package.get("metric")

    # Demand and fulfilment first: the contrast between them is the argument.
    order = (
        "ordered_units",
        "fulfilled_units",
        "fill_rate",
        "available_stock",
        "stockout_rate",
        "net_sales",
        "order_count",
    )
    phrases = []
    for name in order:
        if name == analysed:
            continue
        item = evidence.get(name)
        if item is None:
            continue
        if item.get("direction") == "flat" and name != "ordered_units":
            continue
        change = item.get("change_pct")
        movement = {
            "up": "rose",
            "down": "fell",
            "flat": "held steady",
        }[item["direction"]]
        if change is None or item["direction"] == "flat":
            phrases.append(f"{label_for(name).lower()} {movement}")
        else:
            phrases.append(f"{label_for(name).lower()} {movement} {abs(change):.0%}")

    if not phrases:
        return ""
    if len(phrases) == 1:
        return f"Over the same period, {phrases[0]}."
    return f"Over the same period, {'; '.join(phrases[:-1])}; and {phrases[-1]}."


def _confidence_sentence(package: dict) -> str:
    confidence = package.get("confidence", {})
    level = confidence.get("level", "unknown")
    met = confidence.get("criteria_met")
    total = confidence.get("criteria_total")
    criteria = confidence.get("criteria", [])

    opening = f"Confidence is {level}: {met} of {total} checks passed."
    failed = [describe_criterion(c["name"]).lower() for c in criteria if not c.get("met")]
    if not failed:
        return opening + " Every check the system applies is satisfied here."
    if len(failed) == 1:
        return f"{opening} The check it did not pass is that {failed[0]}."
    return (
        f"{opening} The checks it did not pass are that "
        f"{', '.join(failed[:-1])}, and {failed[-1]}."
    )


def _impact_sentence(package: dict) -> str:
    impact = package.get("impact")
    if not impact or not impact.get("value"):
        return (
            "No impact estimate could be made for this movement, so its "
            "commercial size is unknown rather than small."
        )
    components = impact.get("components", {})
    shortfall = components.get("shortfall_units")
    price = components.get("realised_selling_price")

    sentence = f"The revenue attached to the unserved demand is about {impact['value']:,.0f}"
    if shortfall is not None and price is not None:
        sentence += (
            f", from roughly {shortfall:,.0f} units that would have been delivered at the "
            f"segment's previous rate, valued at the {price:,.2f} it actually achieves per unit"
        )
    return sentence + ". This is what was at stake, not a measured loss."


def write_briefing(package: dict, tables: dict | None = None) -> Briefing:
    """Compose a readable briefing from a signal's evidence package.

    Takes the dictionary a signal produces, not the signal object, so the same
    function serves a report, a page, a terminal, and the prompt handed to a
    language model.
    """
    lookup = build_name_lookup(tables) if tables else {}
    segment = package.get("segment", "")
    readable = humanise_segment(segment, lookup) if lookup else segment
    metric = package.get("metric", "")

    movement = _format_movement(
        metric, float(package.get("movement", 0.0)), package.get("movement_pct")
    )
    headline = (
        f"{label_for(metric)} in {readable} moved {movement} in the "
        f"{package.get('period')} period, compared with {package.get('comparison_period')}."
    )

    reading_parts = [explain_pattern(package.get("likely_driver", ""))]
    alternative = humanise_statement(
        package.get("alternative_hypothesis", ""), segment, lookup
    )
    note = humanise_statement(package.get("alternative_note", ""), segment, lookup)
    if alternative:
        supported = package.get("alternative_supported")
        lead = (
            "The other explanation that would fit is"
            if supported
            else "The competing explanation would be"
        )
        reading_parts.append(f"{lead} that {alternative[0].lower()}{alternative[1:]} {note}")

    return Briefing(
        headline=headline,
        evidence=_evidence_sentence(package),
        reading=" ".join(part for part in reading_parts if part),
        impact=_impact_sentence(package),
        confidence=_confidence_sentence(package),
        recommendation=humanise_statement(
            package.get("recommended_investigation", ""), segment, lookup
        ),
        caveats=(
            "This describes what the evidence is consistent with. It is not a "
            "demonstrated cause.",
            "Confidence counts named checks; it is not a probability.",
            "The impact figure is an estimate of revenue at stake, and its "
            "assumptions all push it high.",
        ),
    )


def write_summary(packages: list[dict], tables: dict | None = None) -> str:
    """One paragraph covering several signals, ordered as they were ranked."""
    if not packages:
        return "Nothing stood out in this period at the confidence required."

    lookup = build_name_lookup(tables) if tables else {}
    lines = []
    for position, package in enumerate(packages, start=1):
        readable = humanise_segment(package.get("segment", ""), lookup)
        impact = package.get("impact") or {}
        value = impact.get("value")
        worth = f", worth roughly {value:,.0f}" if value else ""
        reading = explain_pattern(package.get("likely_driver", "")).rstrip(".")
        lines.append(
            f"{position}. {readable}: {reading}{worth} "
            f"({package.get('confidence', {}).get('level', 'unknown')} confidence)."
        )

    leading = humanise_segment(packages[0].get("segment", ""), lookup)
    opening = (
        f"{len(packages)} movement(s) are worth a look this period. "
        f"The largest sits in {leading}."
    )
    return opening + "\n\n" + "\n".join(lines)
