from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rootsignal.cleaning import CleaningResult, clean_dataset
from rootsignal.ingestion import load_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def generated_dataset(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Generate the controlled sample dataset once per test session.

    The generator is deterministic (seed 42), so a single run is safe to
    share across tests and avoids paying the subprocess cost repeatedly.
    """
    output_dir = tmp_path_factory.mktemp("sample_data")
    subprocess.run(
        ["python", str(ROOT / "scripts" / "generate_sample_data.py"), "--output-dir", str(output_dir)],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return load_dataset(output_dir)


@pytest.fixture(scope="session")
def cleaned_dataset(generated_dataset: dict) -> CleaningResult:
    """Cleaned view of the shared sample dataset."""
    return clean_dataset(generated_dataset)
