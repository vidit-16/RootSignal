import pandas as pd
import pytest

from rootsignal.modeling import build_commercial_mart, enrich_orders, enrich_sales


def make_tables() -> dict[str, pd.DataFrame]:
    date = pd.to_datetime(["2026-01-01"]).date[0]
    return {
        "dim_date": pd.DataFrame(
            {"date": [date], "week": [1], "month": [1], "quarter": ["Q1"], "year": [2026]}
        ),
        "dim_kam": pd.DataFrame({"kam_id": ["K1"], "kam_name": ["KAM One"]}),
        "dim_region": pd.DataFrame(
            {"region_code": ["BLR"], "city": ["Bengaluru"], "zone": ["South"]}
        ),
        "dim_sku": pd.DataFrame(
            {
                "sku_id": ["S1", "S2"],
                "sku_name": ["Apple", "Banana"],
                "category": ["Fruits", "Fruits"],
                "sub_category": ["Standard", "Premium"],
                "pack_size_kg": [1.0, 0.5],
                "unit_cost": [70.0, 80.0],
                "list_price": [100.0, 120.0],
                "active_flag": [True, True],
            }
        ),
        "dim_customer": pd.DataFrame(
            {
                "customer_id": ["C1"],
                "customer_name": ["Customer One"],
                "customer_type": ["Retail Chain"],
                "channel": ["Modern Trade"],
                "region_code": ["BLR"],
                "city": ["Bengaluru"],
                "zone": ["South"],
                "kam_id": ["K1"],
            }
        ),
        "fact_sales": pd.DataFrame(
            {
                "order_id": ["O1", "O1"],
                "date": [date, date],
                "customer_id": ["C1", "C1"],
                "region_code": ["BLR", "BLR"],
                "channel": ["Modern Trade", "Modern Trade"],
                "kam_id": ["K1", "K1"],
                "sku_id": ["S1", "S2"],
                "category": ["Fruits", "Fruits"],
                "sales_type": ["PRIMARY", "PRIMARY"],
                "units": [2, 3],
                "unit_price": [100.0, 120.0],
                "discount_pct": [0.0, 0.0],
                "net_sales": [200.0, 360.0],
            }
        ),
        "fact_orders": pd.DataFrame(
            {
                "order_id": ["O1", "O1"],
                "date": [date, date],
                "customer_id": ["C1", "C1"],
                "region_code": ["BLR", "BLR"],
                "channel": ["Modern Trade", "Modern Trade"],
                "sku_id": ["S1", "S2"],
                "ordered_units": [4, 3],
                "fulfilled_units": [2, 3],
                "cancelled_units": [2, 0],
                "order_status": ["Completed", "Completed"],
                "sales_type": ["PRIMARY", "PRIMARY"],
            }
        ),
        "fact_inventory": pd.DataFrame(
            columns=[
                "date",
                "sku_id",
                "warehouse",
                "region_code",
                "opening_stock",
                "received_units",
                "available_stock",
                "ordered_units",
                "fulfilled_units",
                "stockout_flag",
            ]
        ),
        "fact_targets": pd.DataFrame(
            columns=[
                "date",
                "region_code",
                "category",
                "channel",
                "sales_target",
                "order_target",
                "fill_rate_target",
            ]
        ),
    }


def test_enrichment_preserves_fact_grain() -> None:
    tables = make_tables()

    sales = enrich_sales(tables)
    orders = enrich_orders(tables)

    assert len(sales) == 2
    assert len(orders) == 2
    assert set(sales["kam_name"]) == {"KAM One"}
    assert set(orders["category"]) == {"Fruits"}


def test_commercial_mart_does_not_multiply_multi_sku_orders() -> None:
    mart = build_commercial_mart(make_tables())
    row = mart.iloc[0]

    assert len(mart) == 1
    assert row["sales_units"] == 5
    assert row["net_sales"] == 560.0
    assert row["sales_order_count"] == 1
    assert row["order_count"] == 1
    assert row["ordered_units"] == 7
    assert row["fulfilled_units"] == 5
    assert row["cancelled_units"] == 2
    assert row["fill_rate"] == 0.7143
    assert row["cancellation_rate"] == 0.2857
    assert row["aov"] == 560.0


def test_commercial_mart_requires_business_grain() -> None:
    with pytest.raises(ValueError):
        build_commercial_mart(make_tables(), grain=["region_code", "category"])
