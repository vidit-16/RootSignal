from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.analysis import summarise_by_period
from rootsignal.decomposition import (
    compare_dimensions,
    component_coherence,
    concentration,
    decompose_additive,
    decompose_movement,
    decompose_rate,
    dominant_component,
    explain_movement,
    rank_drivers,
    total_movement,
)

WEEK_1 = "2026-02-09"
WEEK_2 = "2026-02-16"


def summary(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["period_start"] = pd.to_datetime(frame["period_start"])
    return frame


def additive_summary() -> pd.DataFrame:
    return summary(
        [
            {"period_start": WEEK_1, "region_code": "BLR", "net_sales": 100.0},
            {"period_start": WEEK_1, "region_code": "MUM", "net_sales": 200.0},
            {"period_start": WEEK_2, "region_code": "BLR", "net_sales": 60.0},
            {"period_start": WEEK_2, "region_code": "MUM", "net_sales": 190.0},
        ]
    )


# --------------------------------------------------------------------------
# Additive decomposition
# --------------------------------------------------------------------------


def test_additive_contributions_reconstruct_the_movement() -> None:
    """The core contract: contributions must add back to the observed change."""
    result = decompose_additive(additive_summary(), "net_sales", ["region_code"])

    assert total_movement(result) == pytest.approx(-50.0)  # 250 - 300
    blr = result[result["segment"] == "BLR"].iloc[0]
    mum = result[result["segment"] == "MUM"].iloc[0]
    assert blr["contribution"] == pytest.approx(-40.0)
    assert mum["contribution"] == pytest.approx(-10.0)
    assert blr["contribution_share"] == pytest.approx(0.8)
    assert mum["contribution_share"] == pytest.approx(0.2)


def test_additive_handles_segments_that_appear_or_disappear() -> None:
    """A segment missing from one period contributes its full value, not nothing."""
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "BLR", "net_sales": 100.0},
            {"period_start": WEEK_2, "region_code": "BLR", "net_sales": 100.0},
            {"period_start": WEEK_2, "region_code": "NEW", "net_sales": 30.0},
        ]
    )
    result = decompose_additive(frame, "net_sales", ["region_code"])

    assert total_movement(result) == pytest.approx(30.0)
    assert result[result["segment"] == "NEW"].iloc[0]["contribution"] == pytest.approx(30.0)


def test_additive_rejects_a_rate_metric() -> None:
    """Subtracting segment rates would attribute a rate movement incorrectly."""
    with pytest.raises(ValueError, match="not an additive metric"):
        decompose_additive(additive_summary(), "fill_rate", ["region_code"])


def test_shares_are_suppressed_when_segments_offset() -> None:
    """Large opposing moves leave a small residual; shares of it are meaningless.

    Reporting that a segment explains 5,000% of a movement describes the
    arithmetic, not the business.
    """
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "A", "net_sales": 1000.0},
            {"period_start": WEEK_1, "region_code": "B", "net_sales": 1000.0},
            {"period_start": WEEK_2, "region_code": "A", "net_sales": 1500.0},
            {"period_start": WEEK_2, "region_code": "B", "net_sales": 501.0},
        ]
    )
    result = decompose_additive(frame, "net_sales", ["region_code"])

    assert total_movement(result) == pytest.approx(1.0)
    assert result["contribution_share"].isna().all()
    # Magnitude ranking still works when the net share does not.
    assert result["share_of_absolute_movement"].notna().all()


# --------------------------------------------------------------------------
# Rate decomposition
# --------------------------------------------------------------------------


def mix_trap_summary() -> pd.DataFrame:
    """Every segment's fill rate improves, yet the total falls.

    Demand moves from a well-filled segment to a poorly-filled one. Naive
    subtraction of segment rates would report an improvement everywhere and be
    unable to explain the decline at all.
    """
    return summary(
        [
            {"period_start": WEEK_1, "region_code": "A", "fulfilled_units": 90.0, "ordered_units": 100.0},
            {"period_start": WEEK_1, "region_code": "B", "fulfilled_units": 50.0, "ordered_units": 100.0},
            {"period_start": WEEK_2, "region_code": "A", "fulfilled_units": 19.0, "ordered_units": 20.0},
            {"period_start": WEEK_2, "region_code": "B", "fulfilled_units": 99.0, "ordered_units": 180.0},
        ]
    )


def test_rate_decomposition_separates_a_pure_mix_shift_from_a_rate_change() -> None:
    frame = mix_trap_summary()
    result = decompose_rate(frame, "fill_rate", ["region_code"])

    # Total fill rate fell from 140/200 to 118/200.
    assert total_movement(result) == pytest.approx(0.59 - 0.70)

    # Both segments improved, so the rate effect is positive...
    assert result["rate_effect"].sum() == pytest.approx(0.05)
    # ...and the entire decline is mix: demand moved to the weaker segment.
    assert result["mix_effect"].sum() == pytest.approx(-0.16)
    assert result["interaction_effect"].sum() == pytest.approx(0.0, abs=1e-12)
    assert dominant_component(result) == "mix_effect"


def test_rate_effects_reconstruct_the_movement_exactly() -> None:
    """rate + mix + interaction must equal the observed change, with no residual."""
    result = decompose_rate(mix_trap_summary(), "fill_rate", ["region_code"])
    rebuilt = (
        result["rate_effect"].sum()
        + result["mix_effect"].sum()
        + result["interaction_effect"].sum()
    )
    assert rebuilt == pytest.approx(total_movement(result))
    assert (
        result["rate_effect"] + result["mix_effect"] + result["interaction_effect"]
    ).round(10).equals(result["contribution"].round(10))


def test_segment_with_no_volume_in_a_period_is_attributed_to_mix() -> None:
    """Demand entering the business is a mix change, not a rate change."""
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "A", "fulfilled_units": 90.0, "ordered_units": 100.0},
            {"period_start": WEEK_2, "region_code": "A", "fulfilled_units": 90.0, "ordered_units": 100.0},
            {"period_start": WEEK_2, "region_code": "NEW", "fulfilled_units": 25.0, "ordered_units": 50.0},
        ]
    )
    result = decompose_rate(frame, "fill_rate", ["region_code"])
    new_segment = result[result["segment"] == "NEW"].iloc[0]

    assert new_segment["rate_effect"] == pytest.approx(0.0)
    assert new_segment["mix_effect"] != pytest.approx(0.0)


def test_rate_decomposition_rejects_an_unknown_rate() -> None:
    with pytest.raises(ValueError, match="not a known rate"):
        decompose_rate(mix_trap_summary(), "margin_rate", ["region_code"])


def test_rate_decomposition_rejects_a_zero_denominator_period() -> None:
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "A", "fulfilled_units": 0.0, "ordered_units": 0.0},
            {"period_start": WEEK_2, "region_code": "A", "fulfilled_units": 5.0, "ordered_units": 10.0},
        ]
    )
    with pytest.raises(ValueError, match="denominator"):
        decompose_rate(frame, "fill_rate", ["region_code"])


def test_component_coherence_identifies_reshuffling() -> None:
    """A component whose segment effects cancel out has not moved the business."""
    coherence = component_coherence(decompose_rate(mix_trap_summary(), "fill_rate", ["region_code"]))
    interaction = coherence[coherence["component"] == "interaction_effect"].iloc[0]

    assert interaction["net"] == pytest.approx(0.0, abs=1e-9)
    assert coherence.iloc[0]["component"] == "mix_effect"  # ordered by net movement


def test_dominant_component_is_contribution_for_additive_metrics() -> None:
    assert dominant_component(decompose_additive(additive_summary(), "net_sales", ["region_code"])) == (
        "contribution"
    )


# --------------------------------------------------------------------------
# Period selection
# --------------------------------------------------------------------------


def test_decomposition_defaults_to_the_two_most_recent_periods() -> None:
    result = decompose_additive(additive_summary(), "net_sales", ["region_code"])
    assert result["period_start"].iloc[0] == pd.Timestamp(WEEK_2)
    assert result["comparison_period"].iloc[0] == pd.Timestamp(WEEK_1)


def test_decomposition_requires_two_periods() -> None:
    single = summary([{"period_start": WEEK_1, "region_code": "BLR", "net_sales": 100.0}])
    with pytest.raises(ValueError, match="two periods"):
        decompose_additive(single, "net_sales", ["region_code"])


def test_comparison_period_must_precede_the_current_period() -> None:
    with pytest.raises(ValueError, match="must precede"):
        decompose_additive(
            additive_summary(), "net_sales", ["region_code"],
            current_period=WEEK_1, comparison_period=WEEK_2,
        )


def test_decomposition_rejects_a_period_that_is_not_present() -> None:
    with pytest.raises(ValueError, match="not present"):
        decompose_additive(additive_summary(), "net_sales", ["region_code"], current_period="2026-05-01")


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_rank_drivers_orders_by_the_requested_column() -> None:
    result = decompose_rate(mix_trap_summary(), "fill_rate", ["region_code"])

    by_total = rank_drivers(result, direction="negative", top_n=1)
    by_rate = rank_drivers(result, direction="positive", top_n=1, by="rate_effect")

    assert by_total.iloc[0]["ranked_by"] == "contribution"
    assert by_rate.iloc[0]["ranked_by"] == "rate_effect"


def test_rank_drivers_rejects_an_unknown_column() -> None:
    with pytest.raises(ValueError, match="no column"):
        rank_drivers(decompose_additive(additive_summary(), "net_sales", ["region_code"]), by="profit")


def test_concentration_reports_how_localised_a_movement_is() -> None:
    spread = summary(
        [{"period_start": WEEK_1, "region_code": r, "net_sales": 100.0} for r in "ABCD"]
        + [{"period_start": WEEK_2, "region_code": r, "net_sales": 90.0} for r in "ABCD"]
    )
    localised = summary(
        [{"period_start": WEEK_1, "region_code": r, "net_sales": 100.0} for r in "ABCD"]
        + [{"period_start": WEEK_2, "region_code": "A", "net_sales": 60.0}]
        + [{"period_start": WEEK_2, "region_code": r, "net_sales": 100.0} for r in "BCD"]
    )

    assert concentration(decompose_additive(spread, "net_sales", ["region_code"]), top_n=1) == pytest.approx(0.25)
    assert concentration(decompose_additive(localised, "net_sales", ["region_code"]), top_n=1) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Integration with the cleaned pipeline
# --------------------------------------------------------------------------


def test_decomposition_finds_the_disrupted_segments_without_being_told(
    cleaned_dataset, scenarios
) -> None:
    """The seeded supply scenario must be recoverable from the movement alone.

    Nothing here names Bengaluru, Fruits or Vegetables as an input. The
    decomposition is given a fill-rate movement across every region and category
    and must surface the disrupted segments on its own.
    """
    scenario = scenarios["blr_supply_constraint"]
    weekly = summarise_by_period(
        cleaned_dataset.tables, period="week", group_by=["region_code", "category"]
    )
    explanation = explain_movement(
        weekly,
        "fill_rate",
        ["region_code", "category"],
        current_period=scenario["current_period"],
        comparison_period=scenario["comparison_period"],
        top_n=2,
    )

    assert explanation["ranked_by"] == "rate_effect"
    leading = {segment["segment"] for segment in explanation["leading_segments"]}
    assert leading == {"BLR | Fruits", "BLR | Vegetables"}


def test_ranking_a_rate_by_total_contribution_can_hide_a_collapse() -> None:
    """Why rate movements are ranked on their dominant component.

    A segment whose fill rate collapses can still show a *positive* total
    contribution, if demand grew into it at the same time. Ranked on the total
    it reads as a segment that helped, and the collapse never surfaces.

    This is asserted on a constructed case rather than on the sample data: which
    real segment happens to exhibit it depends on that week's demand mix, and a
    property this important should not be tested only when the data obliges.
    """
    frame = summary(
        [
            # Fill rate 0.96, holding a tenth of demand.
            {"period_start": WEEK_1, "region_code": "FAILING", "fulfilled_units": 96.0, "ordered_units": 100.0},
            {"period_start": WEEK_1, "region_code": "STEADY", "fulfilled_units": 855.0, "ordered_units": 900.0},
            # Fill rate collapses to 0.76 while demand grows into the segment.
            {"period_start": WEEK_2, "region_code": "FAILING", "fulfilled_units": 114.0, "ordered_units": 150.0},
            {"period_start": WEEK_2, "region_code": "STEADY", "fulfilled_units": 807.5, "ordered_units": 850.0},
        ]
    )
    result = decompose_rate(frame, "fill_rate", ["region_code"])
    failing = result[result["segment"] == "FAILING"].iloc[0]

    assert failing["rate_effect"] < 0  # fulfilment genuinely deteriorated
    assert failing["contribution"] > 0  # yet the total reads as positive

    ranked_by_total = rank_drivers(result, direction="negative", top_n=2)
    assert "FAILING" not in set(ranked_by_total["segment"])

    ranked_by_rate = rank_drivers(result, direction="negative", top_n=1, by="rate_effect")
    assert ranked_by_rate.iloc[0]["segment"] == "FAILING"


def test_mix_effect_is_incoherent_noise_in_the_real_movement(cleaned_dataset) -> None:
    """Weekly demand reshuffles between fine segments without moving the total."""
    weekly = summarise_by_period(
        cleaned_dataset.tables, period="week", group_by=["region_code", "category"]
    )
    coherence = component_coherence(
        decompose_rate(
            weekly, "fill_rate", ["region_code", "category"],
            current_period="2026-02-16", comparison_period="2026-02-09",
        )
    )
    mix = coherence[coherence["component"] == "mix_effect"].iloc[0]
    rate = coherence[coherence["component"] == "rate_effect"].iloc[0]

    assert mix["gross"] > rate["gross"]  # individually large
    assert mix["coherence"] < 0.05  # but cancelling almost exactly
    assert rate["coherence"] > 0.3  # while the rate effect moved as one


def test_each_dimension_independently_explains_the_whole_movement(cleaned_dataset) -> None:
    """Decompositions are alternative views, never additive with one another.

    Region and category each account for 100% of the same movement. Adding a
    region contribution to a category contribution would double-count it.
    """
    tables = cleaned_dataset.tables
    by_region = summarise_by_period(tables, period="week", group_by=["region_code"])
    by_category = summarise_by_period(tables, period="week", group_by=["category"])

    region_movement = total_movement(decompose_additive(by_region, "net_sales", ["region_code"]))
    category_movement = total_movement(decompose_additive(by_category, "net_sales", ["category"]))

    assert region_movement == pytest.approx(category_movement, rel=1e-6)


def test_compare_dimensions_reports_which_cut_localises_a_movement(cleaned_dataset) -> None:
    tables = cleaned_dataset.tables
    summaries = {
        "region_code": summarise_by_period(tables, period="week", group_by=["region_code"]),
        "category": summarise_by_period(tables, period="week", group_by=["category"]),
        "channel": summarise_by_period(tables, period="week", group_by=["channel"]),
    }
    comparison = compare_dimensions(
        summaries, "net_sales", [["region_code"], ["category"], ["channel"]]
    )

    assert len(comparison) == 3
    assert comparison["top_3_concentration"].is_monotonic_decreasing
    # Every cut describes the same underlying movement.
    assert comparison["total_movement"].nunique() == 1


def test_compare_dimensions_requires_a_summary_for_each_dimension() -> None:
    with pytest.raises(ValueError, match="No period summary supplied"):
        compare_dimensions({}, "net_sales", [["region_code"]])


def test_decompose_movement_dispatches_on_metric_type(cleaned_dataset) -> None:
    weekly = summarise_by_period(cleaned_dataset.tables, period="week", group_by=["region_code"])

    additive = decompose_movement(weekly, "net_sales", ["region_code"])
    rate = decompose_movement(weekly, "fill_rate", ["region_code"])

    assert "rate_effect" not in additive.columns
    assert "rate_effect" in rate.columns


# --------------------------------------------------------------------------
# A volume floor for thin segments
# --------------------------------------------------------------------------


def thin_segment_summary() -> pd.DataFrame:
    """One large segment and several near-empty ones with wild rates.

    The shape real data takes: a dominant market plus a long tail of countries
    carrying a handful of units, where a rate can swing from 0.75 to 0 without
    meaning anything.
    """
    rows = [
        {"period_start": WEEK_1, "region_code": "BIG", "returned_units": 880.0, "sold_units": 10000.0},
        {"period_start": WEEK_2, "region_code": "BIG", "returned_units": 970.0, "sold_units": 10000.0},
    ]
    for name, before, after in (("TINY_A", 3.0, 0.0), ("TINY_B", 2.0, 0.0), ("TINY_C", 4.0, 0.0)):
        rows.append({"period_start": WEEK_1, "region_code": name, "returned_units": before, "sold_units": 4.0})
        rows.append({"period_start": WEEK_2, "region_code": name, "returned_units": after, "sold_units": 4.0})
    return summary(rows)


def test_a_volume_floor_folds_thin_segments_without_dropping_them() -> None:
    """Folding preserves the movement; dropping would change the weights.

    A rate decomposition weights by share of the denominator, so removing
    volume silently changes every weight and can invert which component appears
    to dominate. The thin segments are combined, not discarded.
    """
    frame = thin_segment_summary()

    everything = decompose_rate(frame, "return_rate", ["region_code"])
    folded = decompose_rate(frame, "return_rate", ["region_code"], min_share=0.05)

    assert len(folded) < len(everything)
    assert "Other (below volume floor)" in set(folded["segment"])
    # The observed movement is unchanged: this is a regrouping, not a filter.
    assert total_movement(folded) == pytest.approx(total_movement(everything))


def test_thin_segments_can_invert_which_component_appears_to_dominate() -> None:
    """Why the floor exists at all.

    Segments carrying almost no volume swing wildly and are weighted near zero,
    so they feed noise into mix while saying nothing about the business.
    """
    frame = thin_segment_summary()

    unfiltered = component_coherence(decompose_rate(frame, "return_rate", ["region_code"]))
    floored = component_coherence(
        decompose_rate(frame, "return_rate", ["region_code"], min_share=0.05)
    )

    # The tail contributes a mix effect that shrinks once it is pooled.
    tail_mix = float(unfiltered.loc[unfiltered["component"] == "mix_effect", "gross"].iloc[0])
    pooled_mix = float(floored.loc[floored["component"] == "mix_effect", "gross"].iloc[0])
    assert pooled_mix <= tail_mix


def test_a_floor_that_excludes_nothing_changes_nothing() -> None:
    frame = thin_segment_summary()
    pd.testing.assert_frame_equal(
        decompose_rate(frame, "return_rate", ["region_code"]),
        decompose_rate(frame, "return_rate", ["region_code"], min_share=0.0),
    )


def test_return_rate_is_a_recognised_rate_but_not_a_fill_rate() -> None:
    """Returns and fulfilment are different phenomena with the same arithmetic."""
    from rootsignal.decomposition.contribution import RATE_COMPONENTS

    assert RATE_COMPONENTS["return_rate"] == ("returned_units", "sold_units")
    assert RATE_COMPONENTS["fill_rate"] != RATE_COMPONENTS["return_rate"]


def test_effects_that_do_not_add_back_are_refused() -> None:
    """Plausible-looking effects that explain a movement which did not happen.

    Found on real data: a country returned goods in a month it sold nothing.
    Its numerator has no denominator behind it, so it is weighted at zero and
    drops out of the reconstruction while still counting toward the total.
    """
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "NORTH", "returned_units": 50.0, "sold_units": 1000.0},
            {"period_start": WEEK_2, "region_code": "NORTH", "returned_units": 60.0, "sold_units": 1000.0},
            # Sold nothing this week, but goods bought earlier came back.
            {"period_start": WEEK_1, "region_code": "SOUTH", "returned_units": 0.0, "sold_units": 0.0},
            {"period_start": WEEK_2, "region_code": "SOUTH", "returned_units": 40.0, "sold_units": 0.0},
        ]
    )

    with pytest.raises(ValueError, match="does not reconstruct the movement"):
        decompose_rate(frame, "return_rate", ["region_code"])


def test_the_refusal_names_the_segment_and_the_remedy() -> None:
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "NORTH", "returned_units": 50.0, "sold_units": 1000.0},
            {"period_start": WEEK_2, "region_code": "NORTH", "returned_units": 60.0, "sold_units": 1000.0},
            {"period_start": WEEK_1, "region_code": "SOUTH", "returned_units": 0.0, "sold_units": 0.0},
            {"period_start": WEEK_2, "region_code": "SOUTH", "returned_units": 40.0, "sold_units": 0.0},
        ]
    )

    with pytest.raises(ValueError) as caught:
        decompose_rate(frame, "return_rate", ["region_code"])

    message = str(caught.value)
    assert "SOUTH" in message
    assert "sold_units" in message
    assert "min_share" in message


def test_folding_a_stranded_segment_restores_the_identity() -> None:
    """The remedy the message points at actually works.

    Folded into a bucket that has volume, the stranded numerator is once again
    weighted by a real denominator.
    """
    frame = summary(
        [
            {"period_start": WEEK_1, "region_code": "NORTH", "returned_units": 50.0, "sold_units": 1000.0},
            {"period_start": WEEK_2, "region_code": "NORTH", "returned_units": 60.0, "sold_units": 1000.0},
            {"period_start": WEEK_1, "region_code": "SOUTH", "returned_units": 0.0, "sold_units": 0.0},
            {"period_start": WEEK_2, "region_code": "SOUTH", "returned_units": 40.0, "sold_units": 0.0},
            {"period_start": WEEK_1, "region_code": "TINY", "returned_units": 1.0, "sold_units": 5.0},
            {"period_start": WEEK_2, "region_code": "TINY", "returned_units": 1.0, "sold_units": 5.0},
        ]
    )

    folded = decompose_rate(frame, "return_rate", ["region_code"], min_share=0.05)

    observed = (101.0 / 1005.0) - (51.0 / 1005.0)  # 60 + 40 + 1 against 50 + 0 + 1
    assert total_movement(folded) == pytest.approx(observed)
