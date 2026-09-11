"""The one way in for anything that analyses a dataset.

The project already had the three pieces this composes — a loader that reads CSVs
without coercing them, a cleaning layer that repairs or quarantines what it can,
and a validator that states the contracts each table must satisfy. What it did
not have was a path that used all three. Every command-line entry point but one
called ``clean_dataset(load_dataset(...))`` and went straight to analysis, so the
contracts were checked only in tests and on the external dataset.

That left the project asymmetric in a way its own argument does not survive. The
explanation layer refuses to publish a figure it cannot trace back to the
evidence; the input side would accept a ``fact_sales`` with a column missing and
carry on producing signals from it. A guarantee on the way out is worth much less
when anything at all may come in.

So this module makes the check unavoidable. Errors are judged **after** cleaning,
which is the only point at which a clean bill of health means anything: the
generated sample data contains planted defects on purpose, and the published
external dataset has negative prices and rows that do not reconcile. Both reach
zero errors once cleaned. An error that survives cleaning is therefore not merely
dirty data — it is data the project cannot honestly analyse.
"""

from __future__ import annotations

from pathlib import Path

from .cleaning import clean_dataset
from .cleaning.pipeline import CleaningResult
from .ingestion import load_dataset
from .validation import DatasetValidator, ValidationReport


class DatasetContractError(RuntimeError):
    """A dataset still breached its table contracts after cleaning.

    Carries the report, so a caller that would rather inspect the failures than
    stop can reach them without re-running the validator to find out what went
    wrong.
    """

    def __init__(self, message: str, report: ValidationReport) -> None:
        super().__init__(message)
        self.report = report


def describe_errors(report: ValidationReport) -> str:
    """Name every failing check, so the message says what to go and fix."""
    return "\n".join(
        f"  - {issue.table}.{issue.check}: {issue.message} ({issue.rows:,} row(s))"
        for issue in report.errors()
    )


def check_contracts(tables: dict, *, source: str = "This dataset") -> ValidationReport:
    """Confirm cleaned tables satisfy their contracts, and refuse them if not.

    Warnings are returned rather than raised. They describe data that is
    survivable and worth mentioning — a stockout flag set without low stock, for
    instance — where an error means a table is not the shape the analysis code
    was written against.
    """
    report = DatasetValidator().validate(tables)
    if report.errors():
        raise DatasetContractError(
            f"{source} still breaches its table contracts after cleaning:\n"
            f"{describe_errors(report)}\n"
            "These are not defects cleaning can absorb. Analysing this dataset "
            "would produce figures the project cannot stand behind.",
            report,
        )
    return report


def load_for_analysis(input_dir: Path | str, *, strict: bool = True) -> CleaningResult:
    """Load a dataset, clean it, and confirm it satisfies the table contracts.

    Returns the full :class:`CleaningResult` rather than only the tables, so a
    caller can still report what cleaning changed and what it quarantined.

    ``strict=False`` skips the contract check. It exists for callers that mean to
    inspect a broken dataset rather than be stopped by it — the external-dataset
    script shows a reader what was wrong with the published data — and for tests
    that assert on the failures themselves.
    """
    result = clean_dataset(load_dataset(Path(input_dir)))
    if strict:
        check_contracts(result.tables, source=str(Path(input_dir)))
    return result


def load_tables_for_analysis(input_dir: Path | str, *, strict: bool = True) -> dict:
    """``load_for_analysis`` for the common case of wanting only the tables."""
    return load_for_analysis(input_dir, strict=strict).tables
