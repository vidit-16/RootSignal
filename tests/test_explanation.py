from __future__ import annotations

import pytest

from rootsignal.explanation import (
    CAUSAL_PHRASES,
    explain,
    is_available,
    known_values,
    verify,
    verify_language,
    verify_numbers,
    write_briefing,
    write_summary,
)
from rootsignal.signals import detect_signals


def package(cleaned_dataset, top_n: int = 3) -> list[dict]:
    """Signals for the planted supply constraint, on its own declared window.

    The periods are not written here. The generator publishes them with the
    dataset, so a scenario that moves does not leave this file asserting
    against a window that no longer contains it.
    """
    signals = detect_signals(
        cleaned_dataset.tables,
        metric="fill_rate",
        dimension=["region_code", "category"],
        period="week",
        top_n=top_n,
    )
    return [signal.as_dict() for signal in signals]


# --------------------------------------------------------------------------
# The system is complete without a language model
# --------------------------------------------------------------------------


def test_a_full_explanation_needs_no_api_key(cleaned_dataset) -> None:
    """The deterministic narrator is the default path, not a degraded one.

    Everything the system knows is already in the evidence package, so composing
    it into English requires no model. A reader without a key loses fluency, not
    capability.
    """
    first = package(cleaned_dataset)[0]
    briefing = write_briefing(first, cleaned_dataset.tables)

    assert briefing.headline and briefing.evidence and briefing.reading
    assert briefing.impact and briefing.confidence and briefing.recommendation
    assert len(briefing.as_text()) > 400


def test_explain_falls_back_without_a_key(cleaned_dataset, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = explain(package(cleaned_dataset)[0], cleaned_dataset.tables)

    assert result.source == "deterministic"
    assert result.verified
    assert result.text


def test_availability_is_an_ordinary_state_not_an_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert is_available() is False


# --------------------------------------------------------------------------
# What the briefing says
# --------------------------------------------------------------------------


def test_the_briefing_shows_names_rather_than_codes(cleaned_dataset) -> None:
    briefing = write_briefing(package(cleaned_dataset)[0], cleaned_dataset.tables)
    text = briefing.as_text()

    assert "BLR" not in text
    assert "Bengaluru" in text


def test_the_briefing_keeps_the_alternative_and_the_caveats(cleaned_dataset) -> None:
    """A hedge that gets tidied away in the write-up was never a hedge."""
    briefing = write_briefing(package(cleaned_dataset)[0], cleaned_dataset.tables)

    assert "explanation" in briefing.reading.lower()
    assert "not a measured loss" in briefing.impact
    assert any("not a demonstrated cause" in caveat for caveat in briefing.caveats)


def test_the_briefing_names_the_checks_that_failed(cleaned_dataset) -> None:
    """Saying which check failed is more useful than a level on its own."""
    briefing = write_briefing(package(cleaned_dataset)[0], cleaned_dataset.tables)
    assert "checks passed" in briefing.confidence
    assert "did not pass" in briefing.confidence or "satisfied" in briefing.confidence


def test_the_briefing_never_claims_causation(cleaned_dataset) -> None:
    for entry in package(cleaned_dataset):
        text = write_briefing(entry, cleaned_dataset.tables).as_text().lower()
        for phrase in CAUSAL_PHRASES:
            assert phrase not in text, phrase


def test_the_analysed_metric_is_not_its_own_supporting_evidence(cleaned_dataset) -> None:
    """The headline already stated it; repeating it pads the sentence."""
    briefing = write_briefing(package(cleaned_dataset)[0], cleaned_dataset.tables)
    assert "fill rate fell" not in briefing.evidence.lower()


def test_a_summary_covers_several_signals_in_rank_order(cleaned_dataset) -> None:
    summary = write_summary(package(cleaned_dataset), cleaned_dataset.tables)
    assert "1." in summary and "2." in summary
    assert "Bengaluru" in summary


def test_an_empty_period_is_reported_as_such() -> None:
    assert "Nothing stood out" in write_summary([])


# --------------------------------------------------------------------------
# The numeric guard
# --------------------------------------------------------------------------


SAMPLE = {
    "movement": -0.2681,
    "movement_pct": -0.2757,
    "impact": {"value": 8324.65, "components": {"shortfall_units": 45.3}},
    "confidence": {"criteria_met": 5, "criteria_total": 6},
}


def test_figures_taken_from_the_evidence_are_accepted() -> None:
    text = "Fill rate fell 26.8 points, and about 8,324.65 of revenue was at stake."
    assert verify_numbers(text, SAMPLE).verified


def test_a_rounded_figure_is_still_the_same_figure() -> None:
    """A model writing 8,325 for 8,324.65 is presenting, not inventing."""
    assert verify_numbers("Roughly 8,325 was at stake.", SAMPLE).verified


def test_a_rate_written_as_a_percentage_is_accepted() -> None:
    """0.2681 and 26.8% are the same number shown two ways."""
    assert verify_numbers("Fill rate fell by 26.81%.", SAMPLE).verified


def test_an_invented_figure_is_caught() -> None:
    """This is the control the whole architecture rests on.

    An instruction not to invent numbers is a request. Checking every figure
    against the evidence is a guarantee.
    """
    result = verify_numbers("This cost the business around 45,000 in lost sales.", SAMPLE)

    assert not result.verified
    assert "45,000" in result.unsupported
    assert "not found in the evidence" in result.reason


def test_a_plausible_but_derived_figure_is_still_caught() -> None:
    """Arithmetic the model did itself is exactly what must not get through."""
    result = verify_numbers("Over four weeks that would be 33,298.60.", SAMPLE)
    assert not result.verified


def test_small_integers_are_treated_as_structural() -> None:
    """"5 of 6 checks" and "three paragraphs" are English, not claims."""
    assert verify_numbers("Only 5 of 6 checks passed, across 3 regions.", SAMPLE).verified


def test_known_values_include_readable_forms() -> None:
    values = known_values(SAMPLE)
    assert 8324.65 in values
    assert 8325 in values  # rounded
    assert any(abs(value - 26.81) < 0.01 for value in values)  # as a percentage


# --------------------------------------------------------------------------
# The language guard
# --------------------------------------------------------------------------


def test_causal_phrasing_is_rejected() -> None:
    """The analysis reports what evidence is consistent with, and so must the text."""
    for phrase in ("caused by", "due to", "the root cause", "resulted from"):
        result = verify_language(f"The decline was {phrase} a supply failure.")
        assert not result.acceptable, phrase
        assert phrase in result.found


def test_consistent_with_phrasing_is_accepted() -> None:
    text = "The movement is consistent with a supply constraint, though demand also softened."
    assert verify_language(text).acceptable


def test_both_guards_report_why_they_failed() -> None:
    passed, reason = verify("Sales fell 99,999 due to stockouts.", SAMPLE)
    assert not passed
    assert "99,999" in reason
    assert "causation" in reason


def test_a_faithful_rewrite_passes_both_guards(cleaned_dataset) -> None:
    """A rewrite that stays inside the evidence is accepted, as it should be."""
    first = package(cleaned_dataset)[0]
    briefing = write_briefing(first, cleaned_dataset.tables)

    passed, reason = verify(briefing.as_text(), first)
    assert passed, reason


# --------------------------------------------------------------------------
# The optional rewrite path
# --------------------------------------------------------------------------


def test_an_unverifiable_rewrite_is_discarded(cleaned_dataset, monkeypatch) -> None:
    """A model that invents a figure loses; the deterministic text is returned.

    The rejection is recorded rather than hidden, so a reader can tell that a
    rewrite was attempted and why it was not used.
    """
    first = package(cleaned_dataset)[0]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    class FakeCompletions:
        def create(self, **_):
            class Message:
                content = "Sales collapsed by 92,481 because of a warehouse fire."

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeClient:
        def __init__(self, **_):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    import rootsignal.explanation.llm as llm

    monkeypatch.setattr(llm, "OpenAI", FakeClient, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "openai", type("M", (), {"OpenAI": FakeClient}))

    result = explain(first, cleaned_dataset.tables)

    assert result.source == "deterministic"
    assert "rejected" in result.note
    assert "92,481" not in result.text


def test_a_faithful_rewrite_is_used(cleaned_dataset, monkeypatch) -> None:
    first = package(cleaned_dataset)[0]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    # Built from the signal rather than typed out. The guard passes a rewrite
    # only when every figure in it matches the computed package, so a literal
    # here would turn any change in the data into a failure of the guard.
    faithful = (
        "Deliveries in Bengaluru fell well short of what was ordered this period. "
        "The pattern is consistent with a supply constraint rather than weaker "
        f"demand. Around {first['impact']['value']:,.0f} of revenue was at stake."
    )

    class FakeCompletions:
        def create(self, **_):
            class Message:
                content = faithful

            class Choice:
                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeClient:
        def __init__(self, **_):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setitem(__import__("sys").modules, "openai", type("M", (), {"OpenAI": FakeClient}))

    result = explain(first, cleaned_dataset.tables)

    assert result.source == "language_model"
    assert result.verified
    assert result.text == faithful


def test_a_failed_call_falls_back_rather_than_raising(cleaned_dataset, monkeypatch) -> None:
    """A missing network must not take the explanation down with it."""
    first = package(cleaned_dataset)[0]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    class ExplodingClient:
        def __init__(self, **_):
            raise RuntimeError("no network")

    monkeypatch.setitem(
        __import__("sys").modules, "openai", type("M", (), {"OpenAI": ExplodingClient})
    )

    result = explain(first, cleaned_dataset.tables)

    assert result.source == "deterministic"
    assert "could not be reached" in result.note
    assert result.text


def test_the_prompt_carries_no_raw_data(cleaned_dataset) -> None:
    """The model receives a finished analysis, never a table to analyse."""
    from rootsignal.explanation.llm import SYSTEM_PROMPT, _build_prompt

    first = package(cleaned_dataset)[0]
    prompt = _build_prompt(first, write_briefing(first, cleaned_dataset.tables))

    assert "Never calculate" in SYSTEM_PROMPT
    assert "CONSISTENT WITH" in SYSTEM_PROMPT
    assert "fact_sales" not in prompt
    assert "supporting_evidence" not in prompt


@pytest.mark.parametrize("phrase", ["caused by", "due to", "the root cause"])
def test_the_system_prompt_forbids_the_phrases_the_guard_catches(phrase: str) -> None:
    """Instruction and enforcement should agree about what is not allowed."""
    from rootsignal.explanation.llm import SYSTEM_PROMPT

    assert phrase in SYSTEM_PROMPT.lower()
    assert phrase in CAUSAL_PHRASES


# --------------------------------------------------------------------------
# The numeric guard checks rounding, not proximity
# --------------------------------------------------------------------------


GUARD_PACKAGE = {
    "impact": {"value": 8970.60},
    "evidence": {"fulfilled_change": -0.162, "ordered_change": 0.182},
}


@pytest.mark.parametrize(
    "text",
    [
        "units delivered fell 16%",      # 16.2 written to whole percent
        "units delivered fell 16.2%",
        "impact of 8,971",               # 8970.60 written to the nearest unit
        "impact of 8,970.60",
        "units ordered rose 18%",
    ],
)
def test_a_correctly_rounded_figure_is_accepted(text: str) -> None:
    """The briefing writes percentages to whole numbers, and must pass its own guard.

    A flat proportional tolerance rejected "16%" for an evidence value of 16.2%,
    because the two are 1.23% apart. The guard was refusing the system's own
    faithful text.
    """
    assert verify_numbers(text, GUARD_PACKAGE).verified, text


@pytest.mark.parametrize(
    "text",
    [
        "units delivered fell 19%",
        "units delivered fell 17%",   # further than half a unit from 16.2
        "Sales fell 99,999",
        "a margin of 41.3%",
    ],
)
def test_a_figure_that_is_not_a_rounding_of_the_evidence_is_rejected(text: str) -> None:
    assert not verify_numbers(text, GUARD_PACKAGE).verified, text


def test_the_guard_is_stricter_than_the_tolerance_it_replaced() -> None:
    """8,975 sits within one percent of 8,970.60, and is still a wrong number.

    The proportional tolerance this replaced allowed anything within 89 of the
    real figure. Rounding to the precision the text used allows half a unit.
    """
    assert not verify_numbers("impact of 8,975", GUARD_PACKAGE).verified
    assert verify_numbers("impact of 8,971", GUARD_PACKAGE).verified
