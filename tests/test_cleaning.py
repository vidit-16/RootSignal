from rootsignal.cleaning import clean_dataset


def test_cleaning_removes_recoverable_defects_and_quarantines_invalid_orders(
    generated_dataset,
) -> None:
    result = clean_dataset(generated_dataset)

    assert len(result.tables["fact_sales"]) == len(generated_dataset["fact_sales"].drop_duplicates())
    assert result.tables["fact_sales"]["discount_pct"].isna().sum() == 0
    assert result.tables["fact_sales"]["unit_price"].isna().sum() == 0
    assert result.tables["fact_orders"]["channel"].isna().sum() == 0

    assert len(result.quarantined["fact_orders"]) == 1
    assert not result.audit.empty
    assert "quarantine_invalid_quantities" in set(result.audit["action"])


def test_cleaned_orders_reconcile(cleaned_dataset) -> None:
    orders = cleaned_dataset.tables["fact_orders"]
    assert (orders["fulfilled_units"] <= orders["ordered_units"]).all()
    assert (
        orders["fulfilled_units"] + orders["cancelled_units"]
        == orders["ordered_units"]
    ).all()


def test_cleaned_sales_reconcile(cleaned_dataset) -> None:
    sales = cleaned_dataset.tables["fact_sales"]
    expected = (
        sales["units"] * sales["unit_price"] * (1 - sales["discount_pct"])
    ).round(2)

    assert sales["net_sales"].equals(expected)
