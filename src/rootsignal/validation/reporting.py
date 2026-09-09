from __future__ import annotations

from .validator import ValidationReport


def report_as_dict(report: ValidationReport) -> dict[str, object]:
    return {
        "passed": report.passed,
        "errors": [issue.__dict__ for issue in report.errors()],
        "warnings": [issue.__dict__ for issue in report.warnings()],
    }
