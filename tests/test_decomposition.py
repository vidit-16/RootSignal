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


def test_decomposition_finds_the_disrupted_segments_without_being_told(cleaned_dataset) -> None:
    """The seeded supply scenario must be recoverable from the movement alone.

    Nothing here names Bengaluru, Fruits or Vegetables as an input. The
    decomposition is given a fill-rate movement across every region and category
    and must surface the disrupted segments on its own.
    """
    weekly = summarise_by_period(
        cleaned_dataset.tables, period="week", group_by=["region_code", "category"]
    )
    explanation = explain_movement(
        weekly,
        "fill_rate",
        ["region_code", "category"],
        current_period="2026-02-16",
        comparison_period="2026-02-09",
        top_n=2,
    )

    assert explanation["ranked_by"] == "rate_effect"
    leading = {segment["segment"] for segment in explanation["leading_segments"]}
    assert leading == {"BLR | Fruits", "BLR | Vegetables"}


def test_ranking_a_rate_by_total_contribution_would_hide_the_disruption(cleaned_dataset) -> None:
    """Evidence for why rate movements are ranked on their dominant component.

    BLR Fruits fulfils far less of its demand than before, yet its total
    contribution is positive because demand moved away from it at the same time.
    Ranked on the total it looks like a segment that helped.
    """
    weekly = summarise_by_period(
        cleaned_dataset.tables, period="week", group_by=["region_code", "category"]
    )
    result = decompose_rate(
        weekly, "fill_rate", ["region_code", "category"],
        current_period="2026-02-16", comparison_period="2026-02-09",
    )
    blr_fruits = result[result["segment"] == "BLR | Fruits"].iloc[0]

    assert blr_fruits["rate_effect"] < 0  # fulfilment genuinely deteriorated
    assert blr_fruits["contribution"] > 0  # yet the total reads as positive

    ranked_by_total = rank_drivers(result, direction="negative", top_n=3)
    assert "BLR | Fruits" not in set(ranked_by_total["segment"])


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
