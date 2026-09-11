from pathlib import Path

from rootsignal.ingestion import load_dataset
from rootsignal.validation import DatasetValidator


def test_loader_reads_all_generated_tables(generated_dataset_dir: Path) -> None:
    tables = load_dataset(generated_dataset_dir)
    assert len(tables) == 9
    assert "fact_sales" in tables
    assert "dim_sku" in tables


def test_validator_detects_controlled_quality_issues(generated_dataset) -> None:
    report = DatasetValidator().validate(generated_dataset)
    checks = {issue.check for issue in report.issues}
    assert "duplicate_key" in checks
    assert "required_values" in checks
    assert "fulfilled_not_greater_than_ordered" in checks
    assert not report.passed


def test_validator_accepts_clean_transaction_set(generated_dataset) -> None:
    tables = generated_dataset

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
