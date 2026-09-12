from pathlib import Path

import pandas as pd
import pytest

from rootsignal.ingestion import DATABASE_TABLES, load_dataset
from rootsignal.validation import DatasetValidator, load_csv_directory
from rootsignal.validation.contracts import coerce_dates


def test_loader_reads_all_generated_tables(generated_dataset_dir: Path) -> None:
    tables = load_dataset(generated_dataset_dir)
    assert len(tables) == 10
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


# --------------------------------------------------------------------------
# Database ingestion
# --------------------------------------------------------------------------


def test_tables_can_be_loaded_from_a_live_database(cleaned_dataset) -> None:
    """CSV and Excel are not the only shape operational data arrives in."""
    from rootsignal.ingestion import load_from_database
    from rootsignal.sql import build_database

    tables = cleaned_dataset.tables
    connection = build_database(tables)
    loaded = load_from_database(connection)

    assert set(loaded) == set(DATABASE_TABLES)
    assert len(loaded["fact_sales"]) == len(tables["fact_sales"])
    assert len(loaded["fact_orders"]) == len(tables["fact_orders"])


def test_database_ingestion_reports_missing_tables(cleaned_dataset) -> None:
    from rootsignal.ingestion import load_from_database
    from rootsignal.sql import build_database

    connection = build_database(cleaned_dataset.tables)
    with pytest.raises(ValueError, match="missing expected tables"):
        load_from_database(connection, tables=["fact_sales", "fact_returns"])


def test_database_ingestion_refuses_an_unsafe_table_name(cleaned_dataset) -> None:
    """Table names are interpolated into SQL, so they are checked rather than trusted."""
    from rootsignal.ingestion import load_database_table
    from rootsignal.sql import build_database

    connection = build_database(cleaned_dataset.tables)
    with pytest.raises(ValueError, match="unsafe name"):
        load_database_table(connection, "fact_sales; DROP TABLE dim_sku")


# --- Reading a directory of CSVs ---------------------------------------------
#
# load_csv_directory is exported from the package and is what coerce_dates
# exists to serve. Both were reachable and neither was covered, so a change to
# either would have been caught by nothing.


def test_dates_are_coerced_to_dates_not_timestamps() -> None:
    """Contracts compare dates, so the column has to hold dates."""
    frame = pd.DataFrame({"date": ["2026-02-23", "2026-02-24"], "units": [1, 2]})
    cleaned = coerce_dates(frame)
    assert [str(value) for value in cleaned["date"]] == ["2026-02-23", "2026-02-24"]
    assert cleaned["units"].tolist() == [1, 2], "other columns are untouched"


def test_an_unreadable_date_becomes_missing_rather_than_an_exception() -> None:
    """A bad date is a row to report, not a crash on the way in.

    The required_values check is what turns the resulting gap into an error, so
    coercing quietly here is what lets validation describe the problem instead
    of the loader dying before anything can.
    """
    frame = pd.DataFrame({"date": ["2026-02-23", "not-a-date"]})
    cleaned = coerce_dates(frame)
    assert str(cleaned["date"].iloc[0]) == "2026-02-23"
    assert pd.isna(cleaned["date"].iloc[1])


def test_a_frame_without_the_column_is_returned_unchanged() -> None:
    frame = pd.DataFrame({"sku_id": ["A", "B"]})
    assert coerce_dates(frame).equals(frame)


def test_the_original_frame_is_never_modified() -> None:
    """It copies, so a caller holding the raw frame still holds the raw frame."""
    frame = pd.DataFrame({"date": ["2026-02-23"]})
    coerce_dates(frame)
    assert frame["date"].iloc[0] == "2026-02-23"


def test_load_csv_directory_reads_every_table_and_coerces_dates(tmp_path) -> None:
    pd.DataFrame({"date": ["2026-02-23"], "net_sales": [10.0]}).to_csv(
        tmp_path / "fact_sales.csv", index=False
    )
    pd.DataFrame({"sku_id": ["A"], "list_price": [2.5]}).to_csv(
        tmp_path / "dim_sku.csv", index=False
    )
    tables = load_csv_directory(tmp_path)
    assert set(tables) == {"fact_sales", "dim_sku"}
    assert str(tables["fact_sales"]["date"].iloc[0]) == "2026-02-23"
    assert "date" not in tables["dim_sku"].columns, "left alone when absent"
