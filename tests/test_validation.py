import subprocess
from pathlib import Path

from rootsignal.ingestion import load_dataset
from rootsignal.validation import DatasetValidator

ROOT = Path(__file__).resolve().parents[1]


def make_dataset(tmp_path: Path) -> Path:
    subprocess.run(
        ["python", str(ROOT / "scripts" / "generate_sample_data.py"), "--output-dir", str(tmp_path)],
        check=True,
        cwd=ROOT,
    )
    return tmp_path


def test_loader_reads_all_generated_tables(tmp_path: Path) -> None:
    dataset_dir = make_dataset(tmp_path)
    tables = load_dataset(dataset_dir)
    assert len(tables) == 9
    assert "fact_sales" in tables
    assert "dim_sku" in tables


def test_validator_detects_controlled_quality_issues(tmp_path: Path) -> None:
    dataset_dir = make_dataset(tmp_path)
    tables = load_dataset(dataset_dir)
    report = DatasetValidator().validate(tables)
    checks = {issue.check for issue in report.issues}
    assert "duplicate_key" in checks
    assert "required_values" in checks
    assert "fulfilled_not_greater_than_ordered" in checks
    assert not report.passed


def test_validator_accepts_clean_transaction_set(tmp_path: Path) -> None:
    dataset_dir = make_dataset(tmp_path)
    tables = load_dataset(dataset_dir)

    sales = tables["fact_sales"].drop_duplicates().copy()
    sales["discount_pct"] = sales["discount_pct"].fillna(0.0)
    sku_prices = tables["dim_sku"].set_index("sku_id")["list_price"]
    sales["unit_price"] = sales["unit_price"].fillna(sales["sku_id"].map(sku_prices))
    sales["net_sales"] = (
        sales["units"] * sales["unit_price"] * (1 - sales["discount_pct"])
    ).round(2)
    tables["fact_sales"] = sales

    orders = tables["fact_orders"].copy()
    bad = orders["fulfilled_units"] > orders["ordered_units"]
    orders.loc[bad, "fulfilled_units"] = orders.loc[bad, "ordered_units"]
    orders["cancelled_units"] = orders["ordered_units"] - orders["fulfilled_units"]
    customer_channels = tables["dim_customer"].set_index("customer_id")["channel"]
    orders["channel"] = orders["channel"].fillna(orders["customer_id"].map(customer_channels))
    tables["fact_orders"] = orders

    report = DatasetValidator().validate(tables)
    assert report.passed
    assert not report.errors()
