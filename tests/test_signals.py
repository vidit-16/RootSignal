from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.analysis import summarise_by_period
from rootsignal.impact import (
    estimate_fulfilment_shortfall,
    estimate_unfulfilled_demand,
    realised_selling_price,
)
from rootsignal.signals import (
    DEMAND_SOFTNESS,
    FULFILMENT_CONSTRAINT,
    PORTFOLIO_MIX_SHIFT,
    UNCLASSIFIED,
    assess_confidence,
    classify,
    detect_signals,
    explain_signal,
    periods_of_consistent_movement,
    signals_to_frame,
    evaluate_scenario,
    evaluate_scenarios,
    load_scenarios,
    summarise_evaluation,
    summarise_inventory_by_period,
)

# Language that would turn an association into a causal claim.
CAUSAL_PHRASES = ("caused by", "because of", "due to", "the root cause", "proves", "resulted from")


def observation(before: float, after: float, direction: str) -> dict:
    change = after - before
    return {
        "before": before,
        "after": after,
        "change": change,
        "change_pct": change / abs(before) if before else None,
        "direction": direction,
    }


# --------------------------------------------------------------------------
# Impact estimation
# --------------------------------------------------------------------------


def test_shortfall_values_units_lost_against_the_prior_fill_rate() -> None:
    """The estimate isolates the deterioration, not all unfulfilled demand.

    A segment that has always fulfilled 90% is not losing money daily by failing
    to reach 100%; what is worth quantifying is the gap that opened up.
    """
    estimate = estimate_fulfilment_shortfall(
        ordered_units=200.0, fulfilled_units=150.0, baseline_fill_rate=0.9, net_sales=1500.0
    )
    # Expected at the prior rate: 200 x 0.9 = 180. Shortfall: 30 units.
    # Realised price: 1500 / 150 = 10.0.
    assert estimate.components["expected_fulfilled_units"] == pytest.approx(180.0)
    assert estimate.components["shortfall_units"] == pytest.approx(30.0)
    assert estimate.value == pytest.approx(300.0)


def test_shortfall_is_zero_when_fulfilment_improved() -> None:
    """An improvement is not a negative loss."""
    estimate = estimate_fulfilment_shortfall(
        ordered_units=100.0, fulfilled_units=95.0, baseline_fill_rate=0.8, net_sales=950.0
    )
    assert estimate.value == 0.0


def test_realised_price_reflects_discounting() -> None:
    """Valuing at list price would inflate the estimate with prices nobody paid."""
    assert realised_selling_price(net_sales=900.0, units=10.0) == pytest.approx(90.0)
    with pytest.raises(ValueError, match="without fulfilled units"):
        realised_selling_price(net_sales=900.0, units=0.0)


def test_every_impact_estimate_carries_its_basis_and_assumptions() -> None:
    """An impact figure without its basis invites being quoted as a measured loss."""
    for estimate in (
        estimate_fulfilment_shortfall(100.0, 80.0, 0.95, 800.0),
        estimate_unfulfilled_demand(100.0, 80.0, 800.0),
    ):
        assert estimate.basis
        assert len(estimate.assumptions) >= 3
        assert any("not a measured loss" in assumption for assumption in estimate.assumptions)
        assert estimate.components


def test_unfulfilled_demand_is_broader_than_the_shortfall() -> None:
    """Measuring against a perfect fill rate counts structural unfulfilment too."""
    shortfall = estimate_fulfilment_shortfall(100.0, 80.0, 0.9, 800.0)
    total = estimate_unfulfilled_demand(100.0, 80.0, 800.0)
    assert total.value > shortfall.value


def test_impact_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        estimate_fulfilment_shortfall(-1.0, 0.0, 0.9, 0.0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        estimate_fulfilment_shortfall(100.0, 90.0, 1.4, 900.0)


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


def test_inventory_levels_are_averaged_and_flows_are_summed(cleaned_dataset) -> None:
    """Stock present on every day of a week is not seven times larger than itself."""
    tables = cleaned_dataset.tables
    weekly = summarise_inventory_by_period(tables, period="week", group_by=["region_code"])
    daily = summarise_inventory_by_period(tables, period="day", group_by=["region_code"])

    region = "BLR"
    week_start = pd.Timestamp("2026-01-05")
    week_days = pd.date_range(week_start, periods=7, freq="D")

    weekly_row = weekly[
        (weekly["region_code"] == region) & (weekly["period_start"] == week_start)
    ].iloc[0]
    daily_rows = daily[
        (daily["region_code"] == region) & (daily["period_start"].isin(week_days))
    ]

    # A level: averaged across the days of the week.
    assert weekly_row["available_stock"] == pytest.approx(daily_rows["available_stock"].mean(), rel=1e-6)
    # A flow: summed across them.
    assert weekly_row["received_units"] == pytest.approx(daily_rows["received_units"].sum(), rel=1e-6)


def test_inventory_rejects_a_grain_it_cannot_support(cleaned_dataset) -> None:
    """Inventory is not held by customer, so it cannot corroborate at that grain."""
    with pytest.raises(ValueError, match="cannot be grouped"):
        summarise_inventory_by_period(cleaned_dataset.tables, group_by=["customer_id"])


def test_inventory_rejects_a_fact_missing_its_measures() -> None:
    """A malformed inventory fact fails here rather than inside an aggregation."""
    tables = {"fact_inventory": pd.DataFrame({"date": [], "region_code": []})}
    with pytest.raises(ValueError, match="missing required columns"):
        summarise_inventory_by_period(tables, group_by=["region_code"])


def test_persistence_counts_consecutive_same_direction_periods() -> None:
    summary = pd.DataFrame(
        {
            "period_start": pd.to_datetime(
                ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]
            ),
            "region_code": ["BLR"] * 4,
            "fill_rate": [0.99, 0.95, 0.90, 0.85],
        }
    )
    streak = periods_of_consistent_movement(
        summary, "fill_rate", ["region_code"], ["BLR"], pd.Timestamp("2026-01-26")
    )
    assert streak == 3


def test_persistence_resets_when_direction_flips() -> None:
    summary = pd.DataFrame(
        {
            "period_start": pd.to_datetime(["2026-01-05", "2026-01-12", "2026-01-19"]),
            "region_code": ["BLR"] * 3,
            "fill_rate": [0.99, 0.80, 0.85],
        }
    )
    streak = periods_of_consistent_movement(
        summary, "fill_rate", ["region_code"], ["BLR"], pd.Timestamp("2026-01-19")
    )
    assert streak == 1


# --------------------------------------------------------------------------
# Pattern classification
# --------------------------------------------------------------------------


def test_rising_demand_with_falling_fill_rate_is_a_fulfilment_constraint() -> None:
    """Regression: shipping more units is not evidence that fulfilment held up.

    A segment whose demand rose 38% while shipments rose 9% served a far smaller
    share of its orders. Requiring fulfilled units to fall in absolute terms
    missed exactly this case, which is the commonest shape a supply constraint
    takes in a growing segment.
    """
    evidence = {
        "ordered_units": observation(167.0, 230.0, "up"),
        "fulfilled_units": observation(161.0, 175.0, "up"),
        "fill_rate": observation(0.964, 0.761, "down"),
        "available_stock": observation(28.8, 18.3, "down"),
        "stockout_rate": observation(0.0, 0.214, "up"),
    }
    assessment = classify(evidence, "BLR | Fruits")

    assert assessment.pattern == FULFILMENT_CONSTRAINT
    assert assessment.alternative_supported is False
    assert any("more slowly than demand" in item for item in assessment.corroborating)


def test_falling_fulfilment_with_steady_demand_is_a_fulfilment_constraint() -> None:
    evidence = {
        "ordered_units": observation(100.0, 102.0, "flat"),
        "fulfilled_units": observation(95.0, 70.0, "down"),
        "fill_rate": observation(0.95, 0.686, "down"),
    }
    assessment = classify(evidence, "BLR | Vegetables")
    assert assessment.pattern == FULFILMENT_CONSTRAINT


def test_falling_demand_is_classified_as_demand_softness() -> None:
    evidence = {
        "ordered_units": observation(100.0, 60.0, "down"),
        "fulfilled_units": observation(95.0, 57.0, "down"),
        "fill_rate": observation(0.95, 0.95, "flat"),
        "available_stock": observation(50.0, 52.0, "flat"),
    }
    assessment = classify(evidence, "MUM | Herbs")

    assert assessment.pattern == DEMAND_SOFTNESS
    # Suppressed demand can look like weak demand; stock held, so it is not that.
    assert assessment.alternative_supported is False


def test_demand_softness_flags_suppressed_demand_as_a_live_alternative() -> None:
    """If stock ran short before orders fell, the ordering may be a response."""
    evidence = {
        "ordered_units": observation(100.0, 60.0, "down"),
        "fulfilled_units": observation(95.0, 57.0, "down"),
        "available_stock": observation(50.0, 10.0, "down"),
        "stockout_rate": observation(0.0, 0.4, "up"),
    }
    assessment = classify(evidence, "BLR | Fruits")
    assert assessment.pattern == DEMAND_SOFTNESS
    assert assessment.alternative_supported is True


def test_mix_dominated_movement_is_not_read_as_a_supply_problem() -> None:
    evidence = {
        "ordered_units": observation(100.0, 100.0, "flat"),
        "fulfilled_units": observation(95.0, 80.0, "down"),
    }
    assessment = classify(evidence, "Total", dominant_component="mix_effect")
    assert assessment.pattern == PORTFOLIO_MIX_SHIFT


def test_ambiguous_evidence_is_left_unclassified() -> None:
    """Refusing to name a pattern is better than inventing one."""
    evidence = {
        "ordered_units": observation(100.0, 101.0, "flat"),
        "fulfilled_units": observation(95.0, 96.0, "flat"),
    }
    assessment = classify(evidence, "DEL | Herbs")
    assert assessment.pattern == UNCLASSIFIED
    assert assessment.alternative_supported is None


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def strong_confidence_inputs(**overrides):
    inputs = {
        "movement": -0.20,
        "typical_movement": 0.05,
        "persistence": 3,
        "share_of_absolute_movement": 0.40,
        "corroborating_count": 4,
        "alternative_supported": False,
        "component_coherence": 0.65,
    }
    inputs.update(overrides)
    return inputs


def test_confidence_is_a_count_of_named_criteria() -> None:
    """Not a tuned score: every criterion is inspectable and arguable."""
    assessment = assess_confidence(**strong_confidence_inputs())

    assert assessment.level == "high"
    assert assessment.met == assessment.total
    assert all(criterion.detail for criterion in assessment.criteria)
    assert {criterion.name for criterion in assessment.criteria} == {
        "movement_stands_out",
        "movement_persisted",
        "segment_carries_the_movement",
        "other_metrics_agree",
        "alternative_not_supported",
        "movement_is_directional",
    }


def test_weak_evidence_yields_low_confidence() -> None:
    assessment = assess_confidence(
        movement=-0.01,
        typical_movement=0.05,
        persistence=1,
        share_of_absolute_movement=0.02,
        corroborating_count=0,
        alternative_supported=True,
        component_coherence=0.01,
    )
    assert assessment.level == "low"
    assert assessment.met == 0


def test_a_volatile_segment_must_move_further_for_the_same_confidence() -> None:
    """Movement is judged against the segment's own history, not a global bar."""
    steady = assess_confidence(**strong_confidence_inputs(typical_movement=0.05))
    volatile = assess_confidence(**strong_confidence_inputs(typical_movement=0.50))

    def met(assessment, name):
        return next(c.met for c in assessment.criteria if c.name == name)

    assert met(steady, "movement_stands_out")
    assert not met(volatile, "movement_stands_out")


def test_a_supported_alternative_costs_confidence() -> None:
    confident = assess_confidence(**strong_confidence_inputs(alternative_supported=False))
    contested = assess_confidence(**strong_confidence_inputs(alternative_supported=True))
    assert contested.met == confident.met - 1


def test_missing_history_does_not_silently_pass_a_criterion() -> None:
    assessment = assess_confidence(**strong_confidence_inputs(typical_movement=None))
    criterion = next(c for c in assessment.criteria if c.name == "movement_stands_out")
    assert not criterion.met
    assert "Not enough history" in criterion.detail


# --------------------------------------------------------------------------
# The engine, end to end
# --------------------------------------------------------------------------


def disruption_signals(tables):
    """Signals for a week the supply constraint fully covers.

    The constraint begins mid-week on 2026-02-18, so the week starting 02-16
    carries only five affected days and ordinary demand noise can dominate it.
    Comparing a fully affected week against a clean one is the fair reading, and
    the transition week is examined separately below.
    """
    return detect_signals(
        tables,
        metric="fill_rate",
        dimension=["region_code", "category"],
        period="week",
        current_period="2026-02-23",
        comparison_period="2026-02-09",
        top_n=3,
    )


def test_engine_identifies_the_disrupted_segments_with_supporting_evidence(cleaned_dataset) -> None:
    """The whole system, answering the question it was built for.

    Nothing here names Bengaluru, Fruits or Vegetables, nor tells the engine that
    the movement is supply-related. It is given a fill-rate movement across every
    region and category and must reach that reading from the evidence.
    """
    signals = disruption_signals(cleaned_dataset.tables)
    assert len(signals) == 3

    top = signals[0]
    assert top.segment in {"BLR | Fruits", "BLR | Vegetables"}
    assert top.pattern.pattern == FULFILMENT_CONSTRAINT
    assert top.confidence.level == "high"
    assert top.impact is not None and top.impact.value > 0

    # The supply reading rests on fulfilment having fallen by more than demand
    # did, which is exactly what a falling fill rate means. Demand may soften
    # somewhat at the same time without changing that.
    evidence = top.supporting_evidence
    assert evidence["fill_rate"]["direction"] == "down"
    ordered_change = evidence["ordered_units"]["change_pct"]
    fulfilled_change = evidence["fulfilled_units"]["change_pct"]
    assert fulfilled_change < ordered_change

    # Inventory corroborates independently of the order book.
    assert evidence["available_stock"]["direction"] == "down"
    assert evidence["stockout_rate"]["direction"] == "up"


def test_signals_are_ranked_by_impact_weighted_by_confidence(cleaned_dataset) -> None:
    signals = disruption_signals(cleaned_dataset.tables)
    scores = [signal.priority_score for signal in signals]
    assert scores == sorted(scores, reverse=True)


def test_signal_dictionary_carries_the_full_evidence_package(cleaned_dataset) -> None:
    """The contract an explanation layer consumes; every number arrives computed."""
    package = disruption_signals(cleaned_dataset.tables)[0].as_dict()
    required = {
        "metric", "segment", "period", "comparison_period", "movement",
        "likely_driver", "statement", "supporting_evidence", "impact",
        "confidence", "alternative_hypothesis", "recommended_investigation",
        "priority_score",
    }
    assert required <= set(package)
    assert package["impact"]["basis"]
    assert package["impact"]["assumptions"]
    assert package["confidence"]["criteria"]


def test_signal_output_never_claims_causation(cleaned_dataset) -> None:
    """Correlation must not be presented as cause anywhere a reader will look."""
    for signal in disruption_signals(cleaned_dataset.tables):
        text = " ".join(
            [
                signal.pattern.statement,
                signal.pattern.recommended_investigation,
                signal.pattern.alternative_note,
                explain_signal(signal),
            ]
        ).lower()
        for phrase in CAUSAL_PHRASES:
            assert phrase not in text, f"causal phrasing '{phrase}' in signal output"
        assert "consistent with" in signal.pattern.statement.lower()


def test_every_signal_states_an_alternative_hypothesis(cleaned_dataset) -> None:
    for signal in disruption_signals(cleaned_dataset.tables):
        assert signal.pattern.alternative_hypothesis
        assert signal.pattern.alternative_note


def test_rendered_explanation_only_restates_computed_figures(cleaned_dataset) -> None:
    signal = disruption_signals(cleaned_dataset.tables)[0]
    text = explain_signal(signal)

    assert signal.segment in text
    assert f"{signal.impact.value:,.2f}" in text
    assert signal.confidence.level in text
    assert "Recommended investigation" in text


def test_engine_notes_when_inventory_evidence_is_unavailable(cleaned_dataset) -> None:
    """A grain without inventory must say so rather than quietly omitting it."""
    signals = detect_signals(
        cleaned_dataset.tables,
        metric="fill_rate",
        dimension=["channel"],
        period="week",
        top_n=1,
    )
    assert signals
    assert any("Inventory evidence is unavailable" in note for note in signals[0].notes)
    assert "available_stock" not in signals[0].supporting_evidence


def test_signals_flatten_into_a_reporting_table(cleaned_dataset) -> None:
    frame = signals_to_frame(disruption_signals(cleaned_dataset.tables))
    assert len(frame) == 3
    assert {"segment", "likely_driver", "impact", "confidence", "priority_score"} <= set(frame.columns)
    assert signals_to_frame([]).empty


def test_a_step_change_is_nearly_invisible_in_the_period_that_straddles_it(
    cleaned_dataset, scenarios
) -> None:
    """An honest limitation, asserted rather than left for a reader to discover.

    The demand scenario begins on a Wednesday. In the week that straddles that
    date only part of the week is affected, and the weekly total barely moves:
    ordered units go 64, then 63, then 38. Nearly the whole decline lands in the
    following week, because that is the first week the change occupies entirely.

    A reader comparing the straddling week against the one before it would
    conclude almost nothing happened. This is why the evaluation compares fully
    affected windows, and why the supply constraint was moved to begin on a week
    boundary: a step change is only visible where a clean period meets an
    affected one.
    """
    scenario = scenarios["hyd_demand_softness"]
    weekly = summarise_by_period(
        cleaned_dataset.tables, period="week", group_by=["region_code", "category"]
    )
    segment = weekly[
        (weekly["region_code"] == scenario["region_code"])
        & (weekly["category"].isin(scenario["categories"]))
    ].set_index("period_start")["ordered_units"]

    starts = pd.Timestamp(scenario["starts"])
    straddling = starts - pd.Timedelta(days=starts.weekday())
    before = straddling - pd.Timedelta(days=7)
    first_full = straddling + pd.Timedelta(days=7)

    straddle_move = segment[straddling] - segment[before]
    full_week_move = segment[first_full] - segment[straddling]

    assert full_week_move < 0, "the decline should be unmistakable once a whole week is affected"
    assert abs(straddle_move) < abs(full_week_move) / 5, (
        "the straddling week should hide most of the movement, "
        f"but moved {straddle_move} against {full_week_move}"
    )


# --------------------------------------------------------------------------
# Scenario evaluation
# --------------------------------------------------------------------------


def test_scenario_ground_truth_travels_with_the_dataset(generated_dataset_dir) -> None:
    """The evaluation reads what was planted rather than restating it."""
    scenarios = load_scenarios(generated_dataset_dir)
    names = {scenario["name"] for scenario in scenarios}

    assert len(scenarios) == 5
    assert {"mum_control", "blr_quiet_period_control"} <= names, "both controls"
    for scenario in scenarios:
        assert scenario["metric"]
        assert scenario["current_period"] and scenario["comparison_period"]


def test_the_dataset_carries_more_than_one_control(generated_dataset_dir) -> None:
    """A false-positive rate measured on one window is an estimate from n=1.

    The two controls are deliberately unalike. mum_control is a region where
    nothing is ever planted; blr_quiet_period_control is the run-up to a region
    that does move later, on a different metric. A detector that smeared a real
    event backwards would pass the first and fail the second.

    They disagree in practice -- one raises twice as many false alarms as the
    other at a medium floor -- which is the point. One control could not have
    shown that.
    """
    scenarios = load_scenarios(generated_dataset_dir)
    controls = [s for s in scenarios if s["expected_pattern"] is None]

    assert len(controls) >= 2, "a single control cannot show the spread"
    assert len({c["region_code"] for c in controls}) > 1, "different regions"
    assert len({c["metric"] for c in controls}) > 1, "different metrics"
    for control in controls:
        assert control["starts"] is None and not control["categories"]


def test_missing_manifest_is_reported_rather_than_assumed(tmp_path) -> None:
    with pytest.raises(ValueError, match="No dataset manifest"):
        load_scenarios(tmp_path)


def test_engine_stays_silent_on_every_control(cleaned_dataset, generated_dataset_dir) -> None:
    """A detector that fires every week is not detecting anything.

    Each control window has no scenario under way, so a strict floor must return
    nothing at all -- on both of them, not on whichever one happens to come
    first in the manifest.
    """
    scenarios = load_scenarios(generated_dataset_dir)
    controls = [s for s in scenarios if s["expected_pattern"] is None]
    assert controls

    for control in controls:
        result = evaluate_scenario(
            cleaned_dataset.tables, control, scenarios, confidence_floor="high"
        )
        assert result.outcome == "correct_silence", control["name"]
        assert result.signals_returned == 0, control["name"]


def test_planted_scenarios_are_classified_correctly_at_medium_confidence(
    cleaned_dataset, generated_dataset_dir
) -> None:
    """Each planted situation must be read as the situation it is.

    Finding the one thing that was planted only shows the engine was pointed at
    it. Telling a supply constraint, a demand decline and a mix shift apart is
    the claim worth making.
    """
    scenarios = load_scenarios(generated_dataset_dir)
    results = evaluate_scenarios(cleaned_dataset.tables, scenarios, confidence_floors=("medium",))
    planted = results[results["expected_pattern"].notna()]

    assert len(planted) == 3
    assert (planted["outcome"] == "hit").all(), planted[["scenario", "outcome", "matched_pattern"]]


def test_a_strict_confidence_floor_trades_recall_for_silence(
    cleaned_dataset, generated_dataset_dir
) -> None:
    """The trade-off is measured rather than hidden behind one threshold.

    At a high floor the engine raises nothing on the control and nothing it
    cannot support, and misses the subtlest of the three planted situations. At
    a medium floor it finds all three and admits false alarms.
    """
    scenarios = load_scenarios(generated_dataset_dir)
    results = evaluate_scenarios(cleaned_dataset.tables, scenarios)
    summary = summarise_evaluation(results).set_index("confidence_floor")

    high, medium = summary.loc["high"], summary.loc["medium"]

    assert high["false_alarm_signals"] == 0
    assert high["controls_silent"] == high["controls"]
    assert medium["recall"] > high["recall"]
    assert medium["false_alarm_signals"] > high["false_alarm_signals"]
