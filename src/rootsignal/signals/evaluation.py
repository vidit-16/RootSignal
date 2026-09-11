"""Measure whether the signal engine tells different situations apart.

Validating a diagnostic system against one planted event only shows that it
finds what it was pointed at. The sample dataset therefore carries four
deliberately different situations — a supply constraint, a demand decline, a
mix shift, and a region where nothing happens — and this module checks, for
each, whether the engine reaches the right reading.

The control matters as much as the rest. A detector that flags something every
week is not detecting anything, so the region with nothing planted in it is
scored on whether the engine stays quiet.

Results are reported as measured, including misses. The point of an evaluation
is to find out where a system fails, not to confirm that it works.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .engine import detect_signals

MANIFEST_NAME = "dataset_manifest.json"

HIT = "hit"
WRONG_PATTERN = "wrong_pattern"
MISS = "miss"
CORRECT_SILENCE = "correct_silence"
FALSE_ALARM = "false_alarm"


@dataclass(frozen=True)
class ScenarioResult:
    """How the engine handled one planted situation."""

    scenario: str
    expected_pattern: str | None
    region: str
    metric: str
    confidence_floor: str
    outcome: str
    matched_segment: str | None
    matched_pattern: str | None
    rank: int | None
    signals_returned: int
    false_positives: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "expected_pattern": self.expected_pattern,
            "region": self.region,
            "metric": self.metric,
            "confidence_floor": self.confidence_floor,
            "outcome": self.outcome,
            "matched_segment": self.matched_segment,
            "matched_pattern": self.matched_pattern,
            "rank": self.rank,
            "signals_returned": self.signals_returned,
            "false_positives": list(self.false_positives),
        }


def load_scenarios(dataset_dir: str | Path) -> list[dict]:
    """Read the planted scenarios recorded alongside the generated data.

    Ground truth travels with the dataset rather than being restated here, so
    the evaluation cannot drift away from what was actually generated.
    """
    manifest_path = Path(dataset_dir) / MANIFEST_NAME
    if not manifest_path.exists():
        raise ValueError(f"No dataset manifest at {manifest_path}; regenerate the sample data.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios")
    if not scenarios:
        raise ValueError("The dataset manifest records no scenarios to evaluate against.")
    return scenarios


def _active_regions(scenarios: Sequence[dict], period: str) -> set[str]:
    """Regions with a scenario already under way at a given period."""
    moment = pd.Timestamp(period)
    active = set()
    for scenario in scenarios:
        starts = scenario.get("starts")
        if starts and pd.Timestamp(starts) <= moment:
            active.add(scenario["region_code"])
    return active


def evaluate_scenario(
    tables: dict[str, pd.DataFrame],
    scenario: dict,
    scenarios: Sequence[dict],
    confidence_floor: str = "high",
    dimension: Sequence[str] = ("region_code", "category"),
    top_n: int = 6,
) -> ScenarioResult:
    """Run the engine over one scenario's window and score what came back."""
    signals = detect_signals(
        tables,
        metric=scenario["metric"],
        dimension=list(dimension),
        period="week",
        current_period=scenario["current_period"],
        comparison_period=scenario["comparison_period"],
        top_n=top_n,
        min_confidence=confidence_floor,
    )

    # A signal is a false alarm when its region had nothing under way.
    active = _active_regions(scenarios, scenario["current_period"])
    false_positives = tuple(
        signal.segment
        for signal in signals
        if signal.segment.split(" | ")[0] not in active
    )

    expected = scenario["expected_pattern"]
    if expected is None:
        outcome = FALSE_ALARM if false_positives else CORRECT_SILENCE
        return ScenarioResult(
            scenario=scenario["name"],
            expected_pattern=None,
            region=scenario["region_code"],
            metric=scenario["metric"],
            confidence_floor=confidence_floor,
            outcome=outcome,
            matched_segment=None,
            matched_pattern=None,
            rank=None,
            signals_returned=len(signals),
            false_positives=false_positives,
        )

    categories = set(scenario.get("categories") or [])
    for position, signal in enumerate(signals, start=1):
        region, _, category = signal.segment.partition(" | ")
        if region != scenario["region_code"]:
            continue
        if categories and category not in categories:
            continue
        matched = signal.pattern.pattern == expected
        return ScenarioResult(
            scenario=scenario["name"],
            expected_pattern=expected,
            region=scenario["region_code"],
            metric=scenario["metric"],
            confidence_floor=confidence_floor,
            outcome=HIT if matched else WRONG_PATTERN,
            matched_segment=signal.segment,
            matched_pattern=signal.pattern.pattern,
            rank=position,
            signals_returned=len(signals),
            false_positives=false_positives,
        )

    return ScenarioResult(
        scenario=scenario["name"],
        expected_pattern=expected,
        region=scenario["region_code"],
        metric=scenario["metric"],
        confidence_floor=confidence_floor,
        outcome=MISS,
        matched_segment=None,
        matched_pattern=None,
        rank=None,
        signals_returned=len(signals),
        false_positives=false_positives,
    )


def evaluate_scenarios(
    tables: dict[str, pd.DataFrame],
    scenarios: Sequence[dict],
    confidence_floors: Sequence[str] = ("high", "medium"),
    dimension: Sequence[str] = ("region_code", "category"),
    top_n: int = 6,
) -> pd.DataFrame:
    """Score every scenario at each confidence floor.

    Sweeping the floor rather than fixing one exposes the trade-off directly:
    a strict floor stays silent on quiet weeks but can miss a subtle movement,
    and a looser one finds more and admits more noise.
    """
    rows = [
        evaluate_scenario(tables, scenario, scenarios, floor, dimension, top_n).as_dict()
        for floor in confidence_floors
        for scenario in scenarios
    ]
    return pd.DataFrame(rows)


def summarise_evaluation(results: pd.DataFrame) -> pd.DataFrame:
    """Reduce scenario outcomes to detection rates per confidence floor."""
    rows = []
    for floor, group in results.groupby("confidence_floor", sort=False):
        planted = group[group["expected_pattern"].notna()]
        controls = group[group["expected_pattern"].isna()]

        hits = int((planted["outcome"] == HIT).sum())
        wrong = int((planted["outcome"] == WRONG_PATTERN).sum())
        missed = int((planted["outcome"] == MISS).sum())
        false_alarms = int(group["false_positives"].map(len).sum())

        rows.append(
            {
                "confidence_floor": floor,
                "planted_scenarios": len(planted),
                "correctly_classified": hits,
                "found_but_misclassified": wrong,
                "missed": missed,
                "recall": round(hits / len(planted), 4) if len(planted) else float("nan"),
                "false_alarm_signals": false_alarms,
                "controls_silent": int((controls["outcome"] == CORRECT_SILENCE).sum()),
                "controls": len(controls),
            }
        )
    return pd.DataFrame(rows)
