from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from rootsignal.cleaning import CleaningResult, clean_dataset
from rootsignal.ingestion import load_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def generated_dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the controlled sample generator once per test session.

    The generator is deterministic (seed 42), so one run serves every test
    and the subprocess cost is paid a single time instead of per test.
    """
    output_dir = tmp_path_factory.mktemp("sample_data")
    subprocess.run(
        ["python", str(ROOT / "scripts" / "generate_sample_data.py"), "--output-dir", str(output_dir)],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return output_dir


@pytest.fixture(scope="session")
def scenarios(generated_dataset_dir: Path) -> dict[str, dict]:
    """The planted scenarios, read from the manifest the generator writes.

    Tests take scenario dates from here rather than repeating them. When the
    supply constraint moved to start on a week boundary, five tests broke on
    dates they had hardcoded — the dates were never theirs to own. Ground truth
    travels with the dataset, so the tests read it from there.
    """
    manifest = json.loads((generated_dataset_dir / "dataset_manifest.json").read_text())
    return {scenario["name"]: scenario for scenario in manifest["scenarios"]}


@pytest.fixture(scope="session")
def _loaded_dataset(generated_dataset_dir: Path) -> dict[str, pd.DataFrame]:
    """Session-wide source tables. Tests receive copies, never this dict."""
    return load_dataset(generated_dataset_dir)


@pytest.fixture
def generated_dataset(_loaded_dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Per-test copy of the raw generated dataset.

    Frames are copied so a test that repairs or reassigns a table cannot
    leak that change into another test sharing the session-wide load.
    """
    return {name: frame.copy() for name, frame in _loaded_dataset.items()}


@pytest.fixture
def cleaned_dataset(generated_dataset: dict[str, pd.DataFrame]) -> CleaningResult:
    """Cleaned view of the sample dataset, isolated per test."""
    return clean_dataset(generated_dataset)
