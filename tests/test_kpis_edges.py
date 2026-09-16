"""Boundary and precision cases for the KPI helpers.

These pin the zero-denominator guards, the unit-denominator case, and the
rounding precision of every derived ratio.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rootsignal.metrics import (
    add_growth,
    calculate_fill_rate,
    calculate_order_kpis,
    calculate_primary_secondary_mix,
    calculate_sales_kpis,
    calculate_target_variance,
)

DAY_1, DAY_2 = pd.to_datetime(["2026-01-01", "2026-01-02"]).date


def test_sales_kpis_round_money_to_two_decimals_with_single_order() -> None:
    sales = pd.DataFrame(
        {
            "order_id": ["O1"],
            "date": [DAY_1],
            "units": [3],
            "unit_price": [11.1111],
            "discount_pct": [0.0],
            "net_sales": [3.337],
        }
    )
    row = calculate_sales_kpis(sales).iloc[0]
    assert row["order_count"] == 1
    assert row["net_sales"] == 3.34
    assert row["gross_sales"] == 33.33
    assert row["discount_value"] == 30.0  # 29.9963 before rounding  # 29.9963 before rounding
    assert row["aov"] == 3.34


def test_order_kpis_zero_and_unit_demand() -> None:
    orders = pd.DataFrame(
        {
            "order_id": ["O1", "O2"],
            "date": [DAY_1, DAY_2],
            "ordered_units": [0, 1],
            "fulfilled_units": [0, 1],
            "cancelled_units": [1, 1],
        }
    )
    result = calculate_order_kpis(orders).set_index("date")
    assert pd.isna(result.loc[DAY_1, "fill_rate"])
    assert pd.isna(result.loc[DAY_1, "cancellation_rate"])
    assert result.loc[DAY_2, "fill_rate"] == 1.0
    assert result.loc[DAY_2, "cancellation_rate"] == 1.0


def test_fill_rate_unit_denominator_and_four_decimal_precision() -> None:
    result = calculate_fill_rate(pd.Series([1, 1]), pd.Series([1, 3]))
    assert result.iloc[0] == 1.0
    assert result.iloc[1] == 0.3333


def test_mix_fills_missing_sales_type_with_zero_and_rounds() -> None:
    sales = pd.DataFrame(
        {
            "date": [DAY_1, DAY_2, DAY_2],
            "sales_type": ["PRIMARY", "PRIMARY", "SECONDARY"],
            "net_sales": [1.0, 1.0, 2.0],
        }
    )
    result = calculate_primary_secondary_mix(sales).set_index("date")
    assert result.loc[DAY_1, "secondary_sales"] == 0
    assert result.loc[DAY_1, "total_sales"] == 1.0
    assert result.loc[DAY_1, "primary_mix"] == 1.0
    assert result.loc[DAY_1, "secondary_mix"] == 0.0
    assert result.loc[DAY_2, "primary_mix"] == 0.3333
    assert result.loc[DAY_2, "secondary_mix"] == 0.6667


def test_mix_with_no_sales_is_undefined() -> None:
    sales = pd.DataFrame(
        {"date": [DAY_1], "sales_type": ["PRIMARY"], "net_sales": [0.0]}
    )
    row = calculate_primary_secondary_mix(sales).iloc[0]
    assert pd.isna(row["primary_mix"])
    assert pd.isna(row["secondary_mix"])


def test_growth_without_groups_handles_zero_and_unit_previous() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
            "net_sales": [0.0, 1.0, 4.0, 5.0],
        }
    )
    result = add_growth(frame, "net_sales")
    assert pd.isna(result.loc[0, "growth_pct"])
    assert pd.isna(result.loc[1, "growth_pct"])
    assert result.loc[2, "growth_pct"] == 3.0
    assert result.loc[3, "growth_pct"] == 0.25
    assert result["previous_value"].tolist()[1:] == [0.0, 1.0, 4.0]


def test_growth_rounds_to_four_decimals() -> None:
    frame = pd.DataFrame({"date": [DAY_1, DAY_2], "net_sales": [3.0, 4.0]})
    assert add_growth(frame, "net_sales").loc[1, "growth_pct"] == 0.3333


def test_target_variance_zero_and_unit_targets() -> None:
    dims = ["date"]
    actuals = pd.DataFrame({"date": [DAY_1, DAY_2], "net_sales": [5.0, 3.0]})
    targets = pd.DataFrame({"date": [DAY_1, DAY_2], "sales_target": [0.0, 1.0]})
    result = calculate_target_variance(actuals, targets, dims).set_index("date")
    assert pd.isna(result.loc[DAY_1, "variance_pct"])
    assert np.isclose(result.loc[DAY_2, "variance_pct"], 2.0)
