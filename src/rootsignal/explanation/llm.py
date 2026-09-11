"""Optionally rephrase a briefing with a language model.

The model receives a finished analysis and rewrites it. It is given no data to
work from beyond the evidence package, it is told not to calculate, and — more
importantly — its output is checked against that package before anything is
returned. Text containing a figure that cannot be traced back, or phrasing that
claims a cause, is discarded and the deterministic briefing is used instead.

That check is the point. An instruction not to invent numbers is a request; a
verification step is a control. The project's central rule is that deterministic
code owns business truth and the model only rephrases it, and this is where that
rule is enforced rather than asserted.

Nothing here is required. Without an API key the system returns the same facts,
written by `narrative.write_briefing`, and no capability is lost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .guards import verify
from .narrative import Briefing, write_briefing

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0

SYSTEM_PROMPT = """\
You rewrite finished business analyses so they read naturally. You are not an \
analyst and you are not being asked to analyse anything.

Rules, all of them absolute:
- Use ONLY the figures given to you. Never calculate, infer, estimate, or \
introduce a number that is not in the input, including totals or percentages \
you could derive.
- Never claim that anything caused anything. The analysis reports what evidence \
is CONSISTENT WITH. Preserve that. Do not write "caused by", "due to", \
"because of", "the root cause", "resulted from", or "led to".
- Keep the alternative explanation. It is not a hedge to be tidied away.
- Keep the caveat that an impact figure is revenue at stake, not a measured loss.
- Write plainly, for a sales or supply manager. No jargon, no bullet lists, no \
headings. Three short paragraphs at most.

If the input seems incomplete, write only what it supports. Do not fill gaps.\
"""


@dataclass(frozen=True)
class Explanation:
    """A written explanation and an honest account of where it came from."""

    text: str
    source: str  # "language_model" or "deterministic"
    verified: bool
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "source": self.source,
            "verified": self.verified,
            "note": self.note,
        }


def is_available(api_key: str | None = None) -> bool:
    """Whether a language model can be reached at all.

    Absence is an ordinary state, not an error: the deterministic narrator is
    the default path and needs none of this.
    """
    if not (api_key or os.environ.get("OPENAI_API_KEY")):
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def _build_prompt(package: dict, briefing: Briefing) -> str:
    """Hand the model the finished analysis, not the raw data."""
    return (
        "Rewrite the following analysis so it reads naturally. Change the wording, "
        "not the substance, and introduce no figure that is not already present.\n\n"
        f"{briefing.as_text()}\n\n"
        "For reference, these are the only figures you may use:\n"
        f"- movement: {package.get('movement')}\n"
        f"- movement as a percentage: {package.get('movement_pct')}\n"
        f"- estimated impact: {(package.get('impact') or {}).get('value')}\n"
        f"- checks passed: {package.get('confidence', {}).get('criteria_met')} "
        f"of {package.get('confidence', {}).get('criteria_total')}\n"
    )


def explain(
    package: dict,
    tables: dict | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Explanation:
    """Explain a signal, using a language model only if one is available and passes.

    ``base_url`` accepts any OpenAI-compatible endpoint, so the same code runs
    against a hosted API or a model on the machine. The deterministic briefing is
    produced first either way, and is what gets returned unless the rewrite is
    verifiably faithful to it.
    """
    briefing = write_briefing(package, tables)
    fallback = Explanation(
        text=briefing.as_text(),
        source="deterministic",
        verified=True,
        note="Written from the evidence package without a language model.",
    )

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        return fallback

    try:
        from openai import OpenAI
    except ImportError:
        return Explanation(
            text=briefing.as_text(),
            source="deterministic",
            verified=True,
            note="The openai package is not installed; install the 'ai' extra to enable rewriting.",
        )

    try:
        client = OpenAI(api_key=key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(package, briefing)},
            ],
            temperature=0.2,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception as error:  # noqa: BLE001 - any failure falls back to the briefing
        return Explanation(
            text=briefing.as_text(),
            source="deterministic",
            verified=True,
            note=f"The language model could not be reached ({type(error).__name__}); "
            "the deterministic briefing is shown instead.",
        )

    if not text:
        return fallback

    passed, reason = verify(text, package)
    if not passed:
        return Explanation(
            text=briefing.as_text(),
            source="deterministic",
            verified=True,
            note=f"The rewrite was rejected and discarded. {reason}",
        )

    return Explanation(
        text=text,
        source="language_model",
        verified=True,
        note="Rewritten by a language model and checked against the evidence.",
    )
