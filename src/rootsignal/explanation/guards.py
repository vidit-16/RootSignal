"""Check that generated prose only restates numbers the system computed.

A language model told not to invent figures will usually comply, and
occasionally will not. Instructions are not a control. This module is the
control: every number appearing in generated text is matched against the numbers
in the evidence package, and text containing a figure that cannot be traced back
is rejected.

The rule this enforces is the one the whole project rests on — deterministic
code calculates business truth, and the model only rephrases it. Enforcing that
mechanically is the difference between a claim and a guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Numbers with optional thousands separators, decimals, sign and percent sign.
#
# The grouped alternative requires at least one ,ddd group. With * it also
# matched the first three digits of an unseparated number, and because
# alternation is ordered that shorter match won: 8970.60 was read as 897 and
# 0.60, and 1550% as 155 and 0%. Faithful text was rejected for figures it had
# copied correctly, and an invented 1230 could pass whenever 123 happened to be
# in the evidence.
NUMBER_PATTERN = re.compile(
    r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"  # 8,970.60
    r"|[-+]?\d+(?:\.\d+)?%?"                 # 8970.60
)

# Dates are removed before figures are looked for. An ISO date shreds into
# fragments under the number pattern — 2026-02-23 becomes 202, -23, 202 — and
# every one of those fragments would be reported as an invented figure. Periods
# are named in almost every sentence the system writes, so without this the
# guard would reject nearly all faithful text.
DATE_PATTERN = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")

# A bare four-digit year is a date too, not a business figure.
YEAR_RANGE = (1900, 2100)

# Small integers are structural rather than factual: "3 of 6 checks", "1.",
# "week 2". Requiring them to appear in the evidence would reject ordinary
# English without protecting anything.
STRUCTURAL_MAX = 12


@dataclass(frozen=True)
class VerificationResult:
    """Whether generated text stayed within the figures it was given."""

    verified: bool
    unsupported: tuple[str, ...] = field(default_factory=tuple)
    checked: int = 0

    @property
    def reason(self) -> str:
        if self.verified:
            return "Every figure in the text appears in the evidence."
        return (
            "Text contains figure(s) not found in the evidence: "
            + ", ".join(self.unsupported)
        )


def _walk(value, into: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        into.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            _walk(item, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, into)


def known_values(package: dict) -> set[float]:
    """Every number the evidence package contains, plus its readable forms.

    A model writing about a rate of 0.2681 will usually say 26.8%, and one
    writing about 8,324.65 will often round to 8,325. Those are the same figure
    presented for a reader, so the accepted set carries them too.
    """
    collected: list[float] = []
    _walk(package, collected)

    accepted: set[float] = set()
    for value in collected:
        accepted.add(value)
        accepted.add(abs(value))
        accepted.add(round(value, 2))
        accepted.add(round(value))
        # Rates are commonly written as percentages.
        if abs(value) <= 1:
            as_percent = value * 100
            accepted.update({as_percent, abs(as_percent), round(as_percent, 1), round(as_percent)})
    return accepted


def _parse(token: str) -> float | None:
    cleaned = token.replace(",", "").rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _decimals(token: str) -> int:
    """How precisely a figure was written, in decimal places."""
    cleaned = token.replace(",", "").rstrip("%")
    _, _, fraction = cleaned.partition(".")
    return len(fraction)


def _is_rounded_form_of(value: float, candidate: float, places: int) -> bool:
    """Whether a figure is what the candidate looks like, written to this precision.

    The briefing writes percentages to whole numbers, so an evidence value of
    16.2% appears as "16%". Measured as a relative difference that is 1.23% off
    and a flat 1% tolerance rejects it, which had the guard refusing the
    system's own faithful text.

    A figure is accepted when the evidence rounds to it at the precision the
    text used. That is what a correct rounding means, and it is stricter than
    the proportional tolerance it replaced, which allowed 8,975 to pass for
    8,970.60 because the two are within one percent of each other.
    """
    return abs(value - candidate) <= 0.5 * (10.0 ** -places)


def _is_structural(value: float) -> bool:
    """A small whole number is a count or a list marker, not a business figure."""
    return abs(value) <= STRUCTURAL_MAX and float(value).is_integer()


def _supported(token: str, value: float, accepted: set[float]) -> bool:
    """Whether the evidence holds this figure, read the way the text wrote it.

    A percent sign says the writer scaled something by a hundred, and the
    evidence keeps the unscaled figure: change_pct is change / abs(before), so a
    cancellation spike of 15.5 is written 1550%. Matching only the face value
    rejected that, and the conversion in known_values cannot reach it either --
    that one converts values at or below one, and a fraction above one is still a
    fraction.

    Dividing by a hundred carries the precision two places with it, so "1550%" is
    still pinned to the fraction it was written from: 15.5 matches and 15.6 does
    not.

    Structural values are excluded from the scaled reading. Without that, any
    small count in the evidence would support a percentage a hundred times its
    size -- five met criteria would vouch for "500%" -- which is a figure no
    evidence here ever stood behind.
    """
    places = _decimals(token)
    if any(_is_rounded_form_of(value, candidate, places) for candidate in accepted):
        return True
    if not token.endswith("%"):
        return False
    return any(
        _is_rounded_form_of(value / 100.0, candidate, places + 2)
        for candidate in accepted
        if not _is_structural(candidate)
    )

def verify_numbers(text: str, package: dict) -> VerificationResult:
    """Confirm every figure in the text traces back to the evidence package."""
    accepted = known_values(package)
    unsupported: list[str] = []
    checked = 0

    scannable = DATE_PATTERN.sub(" ", text or "")
    for token in NUMBER_PATTERN.findall(scannable):
        value = _parse(token)
        if value is None:
            continue
        if _is_structural(value):
            continue
        if float(value).is_integer() and YEAR_RANGE[0] <= value <= YEAR_RANGE[1]:
            continue

        checked += 1
        if _supported(token, value, accepted):
            continue
        unsupported.append(token)

    return VerificationResult(
        verified=not unsupported,
        unsupported=tuple(unsupported),
        checked=checked,
    )


# Phrasing that would turn an association into a claim of cause. The system
# reports what evidence is consistent with, and generated text must not quietly
# upgrade that.
CAUSAL_PHRASES = (
    "caused by",
    "the cause of",
    "the root cause",
    "because of",
    "due to",
    "resulted from",
    "led to",
    "proves",
    "demonstrates that",
    "as a result of",
)


@dataclass(frozen=True)
class LanguageResult:
    """Whether generated text avoided claiming causation."""

    acceptable: bool
    found: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        if self.acceptable:
            return "No causal claims found."
        return "Text claims causation: " + ", ".join(self.found)


def verify_language(text: str) -> LanguageResult:
    """Reject text that states a cause the analysis never established."""
    lowered = (text or "").lower()
    found = tuple(phrase for phrase in CAUSAL_PHRASES if phrase in lowered)
    return LanguageResult(acceptable=not found, found=found)


def verify(text: str, package: dict) -> tuple[bool, str]:
    """Both checks together, with the reason when either fails."""
    numbers = verify_numbers(text, package)
    language = verify_language(text)
    if numbers.verified and language.acceptable:
        return True, numbers.reason
    reasons = [
        result.reason
        for result, ok in ((numbers, numbers.verified), (language, language.acceptable))
        if not ok
    ]
    return False, " ".join(reasons)
