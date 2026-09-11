"""The contract check that stands between a dataset and any analysis of it."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from rootsignal.dataset import (
    DatasetContractError,
    check_contracts,
    load_for_analysis,
    load_tables_for_analysis,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dataset_copy(generated_dataset_dir: Path, tmp_path: Path) -> Path:
    """A writable copy of the sample dataset, for tests that damage it."""
    target = tmp_path / "data"
    shutil.copytree(generated_dataset_dir, target)
    return target


def test_the_generated_dataset_passes_its_contracts_once_cleaned(dataset_copy: Path) -> None:
    """The invariant everything else here depends on.

    The raw sample data breaches its contracts on purpose — planted duplicates,
    nulls and a reconciliation failure give the cleaning layer something real to
    do. What must hold is that none of it survives cleaning.
    """
    result = load_for_analysis(dataset_copy)
    assert len(result.tables) == 10


def test_a_missing_column_is_refused_rather_than_analysed(dataset_copy: Path) -> None:
    """The gap this module was written to close.

    Dropping a column leaves a table the analysis code was not written against.
    Cleaning cannot reinstate it, so the dataset has to be refused; the previous
    path loaded it and went on to produce signals from it.
    """
    sales = pd.read_csv(dataset_copy / "fact_sales.csv")
    sales.drop(columns=["customer_id"]).to_csv(dataset_copy / "fact_sales.csv", index=False)

    with pytest.raises(DatasetContractError) as raised:
        load_for_analysis(dataset_copy)

    assert "required_columns" in str(raised.value)
    assert "customer_id" in str(raised.value)


def test_the_refusal_names_the_checks_that_failed(dataset_copy: Path) -> None:
    """A refusal that does not say what broke only moves the problem."""
    sales = pd.read_csv(dataset_copy / "fact_sales.csv")
    sales["date"] = "not-a-date"
    sales.to_csv(dataset_copy / "fact_sales.csv", index=False)

    with pytest.raises(DatasetContractError) as raised:
        load_tables_for_analysis(dataset_copy)

    message = str(raised.value)
    assert "fact_sales" in message
    assert raised.value.report.errors(), "the report travels with the error"


def test_strict_false_returns_the_data_it_would_otherwise_refuse(dataset_copy: Path) -> None:
    """Inspecting a broken dataset is a legitimate thing to want to do."""
    sales = pd.read_csv(dataset_copy / "fact_sales.csv")
    sales.drop(columns=["customer_id"]).to_csv(dataset_copy / "fact_sales.csv", index=False)

    tables = load_tables_for_analysis(dataset_copy, strict=False)
    assert "fact_sales" in tables


def test_warnings_alone_do_not_refuse_a_dataset(cleaned_dataset) -> None:
    """Only errors stop the analysis.

    The cleaned sample data carries a stockout-flag warning. A layer that
    refused on warnings would refuse the project's own dataset, and would teach
    everyone to pass strict=False.
    """
    report = check_contracts(cleaned_dataset.tables)
    assert not report.errors()
    assert report.issues, "this dataset does still carry a warning"


def test_no_entry_point_loads_a_dataset_without_checking_it() -> None:
    """The structural guard, so the gap cannot quietly reopen.

    Every command-line script once composed the loader and the cleaner directly
    and skipped the contracts entirely. A new script written from the pattern of
    an old one would have inherited that, and nothing would have objected.
    """
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "clean_dataset(load_dataset(" in source:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} load and clean a dataset without checking its contracts; "
        "use rootsignal.dataset.load_tables_for_analysis instead"
    )
