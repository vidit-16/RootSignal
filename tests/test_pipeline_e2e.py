from rootsignal.cleaning import clean_dataset
from rootsignal.modeling.consolidation import build_commercial_mart
from rootsignal.validation import DatasetValidator


def test_generated_data_flows_through_rootsignal_pipeline(generated_dataset) -> None:
    raw_tables = generated_dataset
    raw_report = DatasetValidator().validate(raw_tables)
    assert not raw_report.passed
    assert raw_report.errors()

    cleaning_result = clean_dataset(raw_tables)
    clean_tables = cleaning_result.tables
    clean_report = DatasetValidator().validate(clean_tables)
    assert clean_report.passed

    mart = build_commercial_mart(clean_tables)
    assert not mart.empty
    assert mart["net_sales"].sum().round(2) == clean_tables["fact_sales"]["net_sales"].sum().round(2)
    assert mart["sales_units"].sum() == clean_tables["fact_sales"]["units"].sum()
    assert mart["ordered_units"].sum() == clean_tables["fact_orders"]["ordered_units"].sum()
    assert mart["fulfilled_units"].sum() == clean_tables["fact_orders"]["fulfilled_units"].sum()

    # Order counts are not additive across a grain that splits orders. The mart
    # splits by category, so a basket holding a fruit and a vegetable is one
    # order sitting in two cells: correct within each, double-counted the moment
    # they are added. Summing the mart therefore exceeds the true order count,
    # and no cell may ever exceed it.
    distinct_sales_orders = clean_tables["fact_sales"]["order_id"].nunique()
    assert int(mart["sales_order_count"].sum()) >= distinct_sales_orders
    assert int(mart["sales_order_count"].max()) <= distinct_sales_orders
    # An order belongs to one date, so daily distinct counts do add up.
    daily_orders = clean_tables["fact_sales"].groupby("date")["order_id"].nunique().sum()
    assert daily_orders == distinct_sales_orders
    assert not cleaning_result.audit.empty
    assert len(cleaning_result.quarantined["fact_orders"]) == 1
