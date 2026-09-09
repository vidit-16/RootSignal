from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd


def load_table(path: str | Path, file_type: Literal["csv", "excel"] | None = None) -> pd.DataFrame:
    """Load one tabular source while keeping raw values intact."""
    source = Path(path)
    kind = file_type or source.suffix.lower().lstrip(".")
    if kind == "csv":
        return pd.read_csv(source)
    if kind in {"xlsx", "xls", "excel"}:
        return pd.read_excel(source)
    raise ValueError(f"Unsupported tabular format: {source.suffix}")


def load_dataset(directory: str | Path) -> dict[str, pd.DataFrame]:
    """Load every CSV table from a dataset directory using file stem as table name."""
    path = Path(directory)
    if not path.exists():
        raise FileNotFoundError(f"Dataset directory does not exist: {path}")
    return {p.stem: load_table(p, "csv") for p in sorted(path.glob("*.csv"))}
