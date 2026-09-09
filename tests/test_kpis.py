from __future__ import annotations

import pandas as pd
import pytest

from rootsignal.metrics import (
    add_growth,
    calculate_fill_rate,
    calculate_order_kpis,
    calculate_primary_secondary_mix,
    calculate_sales_kpis,
    calculate_target_variance,
)


def test_sales_kpis_count_orders_without_double_counting_lines() -> None:
    sales = pd.DataFrame(
        {
            "order_id": ["O1", "O1", "O2"],
            "date": pd.to_datetime(["2026-01-01"] * 3).date,
            "sku_id": ["S1", "S2", "S1"],
            "units": [2, 3, 1],
            "unit_price": [100.0, 50.0, 100.0],
            "discount_pct": [0.0, 0.0, 0.1],
            "net_sales": [200.0, 150.0, 90.0],
        }
    )
    result = calculate_sales_kpis(sales, ["date"])
    row = result.iloc[0]
    assert row["order_count"] == 2
    assert row["units"] == 6
    assert row["net_sales"] == 440.0
    assert row["aov"] == 220.0


def test_order_kpis_calculate_fill_and_cancellation_rates() -> None:
    orders = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "date": pd.to_datetime(["2026-01-01", "2026-01-01"]).date,
            "sku_id": ["S1", "S2"],
            "ordered_units": [10, 5],
            "fulfilled_units": [8, 5],
            "cancelled_units": [2, 0],
        }
    )
    result = calculate_order_kpis(orders, ["date"])
    row = result.iloc[0]
    assert row["order_count"] == 2
    assert row["fill_rate"] == 13 / 15
    assert row["cancellation_rate"] == 2 / 15


def test_fill_rate_zero_demand_is_undefined() -> None:
    result = calculate_fill_rate(pd.Series([0, 4]), pd.Series([0, 5]))
    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == 0.8


def test_primary_secondary_mix_sums_to_one_when_sales_exist() -> None:
    sales = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01"] * 3).date,
            "sales_type": ["PRIMARY", "PRIMARY", "SECONDARY"],
            "net_sales": [100.0, 50.0, 50.0],
        }
    )
    result = calculate_primary_secondary_mix(sales, ["date"])
    row = result.iloc[0]
    assert row["total_sales"] == 200.0
    assert row["primary_mix"] == 0.75
    assert row["secondary_mix"] == 0.25


def test_growth_is_calculated_within_group() -> None:
    frame = pd.DataFrame(
        {
            "region_code": ["BLR", "BLR", "MUM", "MUM"],
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-01", "2026-01-02"]).date,
            "net_sales": [100.0, 120.0, 200.0, 180.0],
        }
    )
    result = add_growth(frame, "net_sales", ["region_code"])
    blr = result[result["region_code"] == "BLR"].sort_values("date")
    mum = result[result["region_code"] == "MUM"].sort_values("date")
    assert pd.isna(blr.iloc[0]["growth_pct"])
    assert blr.iloc[1]["growth_pct"] == 0.2
    assert mum.iloc[1]["growth_pct"] == -0.1


def test_target_variance_requires_compatible_grain() -> None:
    actuals = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]).date,
            "region_code": ["BLR", "BLR"],
            "category": ["Fruits", "Fruits"],
            "net_sales": [90.0, 120.0],
        }
    )
    targets = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]).date,
            "region_code": ["BLR", "BLR"],
            "category": ["Fruits", "Fruits"],
            "sales_target": [100.0, 100.0],
        }
    )
    result = calculate_target_variance(
        actuals,
        targets,
        ["date", "region_code", "category"],
    )
    assert result["variance"].tolist() == [-10.0, 20.0]
    assert result["variance_pct"].tolist() == [-0.1, 0.2]


def test_growth_rejects_period_in_group_by() -> None:
    frame = pd.DataFrame({"date": ["2026-01-01"], "net_sales": [10.0]})
    with pytest.raises(ValueError):
        add_growth(frame, "net_sales", ["date"])
