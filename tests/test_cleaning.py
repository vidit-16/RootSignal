import subprocess
from pathlib import Path

from rootsignal.cleaning import clean_dataset
from rootsignal.ingestion import load_dataset

ROOT = Path(__file__).resolve().parents[1]


def make_dataset(tmp_path: Path):
    subprocess.run(
        ["python", str(ROOT / "scripts" / "generate_sample_data.py"), "--output-dir", str(tmp_path)],
        check=True,
        cwd=ROOT,
    )
    return load_dataset(tmp_path)


def test_cleaning_removes_recoverable_defects_and_quarantines_invalid_orders(tmp_path: Path) -> None:
    tables = make_dataset(tmp_path)
    result = clean_dataset(tables)

    assert len(result.tables["fact_sales"]) == len(tables["fact_sales"].drop_duplicates())
    assert result.tables["fact_sales"]["discount_pct"].isna().sum() == 0
    assert result.tables["fact_sales"]["unit_price"].isna().sum() == 0
    assert result.tables["fact_orders"]["channel"].isna().sum() == 0

    assert len(result.quarantined["fact_orders"]) == 1
    assert not result.audit.empty
    assert "quarantine_invalid_quantities" in set(result.audit["action"])


def test_cleaned_orders_reconcile(tmp_path: Path) -> None:
    result = clean_dataset(make_dataset(tmp_path))
    orders = result.tables["fact_orders"]
    assert (orders["fulfilled_units"] <= orders["ordered_units"]).all()
    assert (orders["fulfilled_units"] + orders["cancelled_units"] == orders["ordered_units"]).all()
\n\ndef test_cleaned_sales_reconcile(tmp_path: Path) -> None:\n    result = clean_dataset(make_dataset(tmp_path))\n    sales = result.tables["fact_sales"]\n    expected = (\n        sales["units"] * sales["unit_price"] * (1 - sales["discount_pct"])\n    ).round(2)\n    assert sales["net_sales"].equals(expected)\n