from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.adapters import ANALYSIS_REQUIREMENTS, DatasetCapabilities, empty_fact
from rootsignal.adapters.online_retail import adapt, returns_frame
from rootsignal.analysis import calculate_trend, summarise_by_period
from rootsignal.cleaning import clean_dataset
from rootsignal.decomposition import decompose_additive, total_movement
from rootsignal.validation import DatasetValidator


def raw_lines() -> pd.DataFrame:
    """Invoice lines shaped like the published file, defects included.

    Built here rather than downloaded so the suite needs no network. The shapes
    are the real ones: a repeated product on one invoice, a missing customer, a
    zero price, and a return.
    """
    return pd.DataFrame(
        {
            "Invoice": ["536365", "536365", "536365", "536366", "536367", "C536368", "536369"],
            "StockCode": ["85123A", "71053", "85123A", "84406B", "22633", "84406B", "21730"],
            "Description": [
                "WHITE HANGING HEART T-LIGHT HOLDER",
                "WHITE METAL LANTERN",
                "WHITE HANGING HEART T-LIGHT HOLDER",
                "CREAM CUPID HEARTS COAT HANGER",
                "HAND WARMER UNION JACK",
                "CREAM CUPID HEARTS COAT HANGER",
                "GLASS STAR FROSTED T-LIGHT HOLDER",
            ],
            "Quantity": [6, 6, 4, 8, 6, -8, 6],
            "InvoiceDate": pd.to_datetime(
                [
                    "2009-12-01 08:26", "2009-12-01 08:26", "2009-12-01 08:26",
                    "2009-12-01 08:28", "2009-12-02 09:01", "2009-12-02 09:30",
                    "2009-12-03 10:00",
                ]
            ),
            "Price": [2.55, 3.39, 2.55, 2.75, 0.0, 2.75, 4.25],
            "Customer ID": [17850.0, 17850.0, 17850.0, None, 13047.0, 13047.0, 13047.0],
            "Country": ["United Kingdom"] * 5 + ["France", "France"],
        }
    )


# --------------------------------------------------------------------------
# Capability declaration
# --------------------------------------------------------------------------


def test_an_adapter_declares_what_its_dataset_cannot_answer() -> None:
    """Faking a missing fact would produce answers the data cannot support.

    A fill rate derived from returns would read as a supply failure when
    customers simply sent things back.
    """
    capabilities = DatasetCapabilities(name="Sales only", available_facts=("fact_sales",))

    assert capabilities.supports("forecasting")
    assert capabilities.supports("driver_decomposition")
    assert not capabilities.supports("fulfilment_analysis")
    assert not capabilities.supports("supply_signals")
    assert "fact_orders" in capabilities.missing_facts


def test_an_unsupported_analysis_says_which_fact_is_missing() -> None:
    capabilities = DatasetCapabilities(name="Sales only", available_facts=("fact_sales",))
    reason = capabilities.why_unsupported("supply_signals")

    assert "fact_orders" in reason and "fact_inventory" in reason
    assert capabilities.why_unsupported("forecasting") == ""


def test_unknown_analyses_are_rejected() -> None:
    capabilities = DatasetCapabilities(name="x", available_facts=("fact_sales",))
    with pytest.raises(ValueError, match="Unknown analysis"):
        capabilities.supports("astrology")


def test_every_declared_analysis_has_stated_requirements() -> None:
    for analysis, facts in ANALYSIS_REQUIREMENTS.items():
        assert facts, analysis
        assert all(fact.startswith("fact_") for fact in facts), analysis


def test_an_absent_fact_is_empty_rather_than_invented() -> None:
    """Correctly shaped and empty, so the pipeline runs without inventing rows."""
    orders = empty_fact("fact_orders")
    assert orders.empty
    assert "ordered_units" in orders.columns


# --------------------------------------------------------------------------
# Mapping real invoice data
# --------------------------------------------------------------------------


def test_returns_are_separated_from_sales_rather_than_netted() -> None:
    """A return is a customer sending goods back, not a warehouse failing to ship.

    Netting them into sales would hide both; treating them as unfulfilled demand
    would turn a returns problem into a supply signal.
    """
    dataset = adapt(raw=raw_lines())
    sales = dataset.tables["fact_sales"]

    assert (sales["units"] > 0).all()
    assert not sales["order_id"].str.startswith("C").any()

    returns = returns_frame(raw=raw_lines())
    assert len(returns) == 1
    assert returns["returned_units"].iloc[0] == 8


def test_a_product_repeated_on_one_invoice_is_consolidated() -> None:
    """Real invoices list a product twice; the declared key does not hold as published.

    Units and revenue must survive the consolidation exactly, or the fix would
    be worse than the problem.
    """
    raw = raw_lines()
    dataset = adapt(raw=raw)
    sales = dataset.tables["fact_sales"]

    repeated = sales[(sales["order_id"] == "536365") & (sales["sku_id"] == "85123A")]
    assert len(repeated) == 1
    assert repeated["units"].iloc[0] == 10  # 6 + 4
    assert repeated["net_sales"].iloc[0] == pytest.approx(25.50)  # 15.30 + 10.20

    # The declared grain now holds.
    assert not sales.duplicated(subset=["order_id", "sku_id", "sales_type"]).any()


def test_missing_customers_are_named_rather_than_dropped() -> None:
    """A fifth of real lines have no customer. Dropping them would lose a fifth of revenue."""
    dataset = adapt(raw=raw_lines())
    sales = dataset.tables["fact_sales"]

    assert "UNKNOWN" in set(sales["customer_id"])
    assert sales["customer_id"].notna().all()


def test_the_adapter_leaves_defects_for_validation_to_find() -> None:
    """Cleaning decides what to do about defects; the adapter does not pre-empt it."""
    dataset = adapt(raw=raw_lines())
    sales = dataset.tables["fact_sales"]

    assert (sales["unit_price"] <= 0).any(), "the zero-priced line should survive the mapping"


def test_zero_priced_lines_are_quarantined_not_repaired() -> None:
    """A line priced at zero is not a sale, and no price can be invented for it."""
    dataset = adapt(raw=raw_lines())
    result = clean_dataset(dataset.tables)

    assert "quarantine_unsellable_lines" in set(result.audit["action"])
    assert (result.tables["fact_sales"]["unit_price"] > 0).all()
    assert len(result.quarantined["fact_sales"]) == 1


def test_the_capability_notes_record_what_was_approximated(cleaned_dataset) -> None:
    """A reader must be able to tell a real dimension from a derived one."""
    dataset = adapt(raw=raw_lines())
    notes = " ".join(dataset.capabilities.notes).lower()

    assert "country stands in for region" in notes
    assert "category is not recorded" in notes
    assert "returns" in notes


# --------------------------------------------------------------------------
# The pipeline runs unchanged
# --------------------------------------------------------------------------


def test_the_unmodified_pipeline_runs_on_adapted_data() -> None:
    """Nothing in the analytical layers changes for a different dataset.

    This is what the adapter exists to demonstrate: the pipeline is not built
    around its own generator.
    """
    dataset = adapt(raw=raw_lines())
    tables = clean_dataset(dataset.tables).tables

    daily = summarise_by_period(tables, period="day")
    assert not daily.empty
    assert daily["net_sales"].sum() > 0

    report = DatasetValidator().validate(tables)
    assert not report.errors(), [issue.message for issue in report.errors()]


def test_decomposition_works_across_a_real_dimension() -> None:
    raw = raw_lines()
    dataset = adapt(raw=raw)
    tables = clean_dataset(dataset.tables).tables

    by_region = summarise_by_period(tables, period="day", group_by=["region_code"])
    if by_region[by_region.columns[0]].nunique() < 2:
        pytest.skip("not enough periods in the fixture to decompose a movement")

    decomposition = decompose_additive(by_region, "net_sales", ["region_code"])
    assert len(decomposition) >= 1
    assert total_movement(decomposition) == pytest.approx(
        decomposition["contribution"].sum()
    )


def test_analyses_needing_absent_facts_are_refused_not_faked() -> None:
    """Asking for fulfilment analysis on sales-only data must fail loudly."""
    dataset = adapt(raw=raw_lines())

    with pytest.raises(ValueError, match="fact_orders"):
        dataset.require("fulfilment_analysis")
    dataset.require("forecasting")  # supported, so no error


def test_a_forecast_series_cannot_be_built_for_an_absent_fact() -> None:
    """The forecasting layer refuses an orders series with no order fact behind it."""
    from rootsignal.forecasting import build_daily_series

    dataset = adapt(raw=raw_lines())
    sales = clean_dataset(dataset.tables).tables["fact_sales"]

    with pytest.raises(ValueError, match="fact_orders is required"):
        build_daily_series(sales, metrics=["orders"])

    series = build_daily_series(sales, metrics=["net_sales"])
    assert not series.empty


def test_trend_analysis_runs_on_adapted_data() -> None:
    dataset = adapt(raw=raw_lines())
    tables = clean_dataset(dataset.tables).tables

    daily = summarise_by_period(tables, period="day")
    trend = calculate_trend(daily, "net_sales")
    assert len(trend) == max(len(daily) - 1, 0)
