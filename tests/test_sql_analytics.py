from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rootsignal.analysis import add_period_column
from rootsignal.modeling import build_commercial_mart
from rootsignal.signals import detect_signals
from rootsignal.sql import available_queries, build_database, query, run_query_file

MART_KEY = ["date", "region_code", "category", "channel", "sales_type"]

# Every mart column that must agree exactly with the Python implementation.
# aov is excluded and checked separately: see the tie-break note below.
EXACT_MART_COLUMNS = [
    "gross_sales",
    "net_sales",
    "sales_units",
    "sales_order_count",
    "order_count",
    "ordered_units",
    "fulfilled_units",
    "cancelled_units",
    "fill_rate",
    "cancellation_rate",
    "discount_value",
]


@pytest.fixture
def database(cleaned_dataset):
    """A loaded database per test.

    Function-scoped because cleaned_dataset is: each test gets isolated frames,
    and a wider scope here would depend on a narrower one.
    """
    return build_database(cleaned_dataset.tables)


def _aligned_marts(tables) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = build_database(tables)
    sql_mart = query(conn, "SELECT * FROM mart_commercial_daily")
    python_mart = build_commercial_mart(tables)

    sql_mart["date"] = sql_mart["date"].astype(str)
    python_mart["date"] = python_mart["date"].astype(str)
    return (
        sql_mart.sort_values(MART_KEY).reset_index(drop=True),
        python_mart.sort_values(MART_KEY).reset_index(drop=True),
    )


# --------------------------------------------------------------------------
# One definition of every metric
# --------------------------------------------------------------------------


def test_sql_mart_matches_the_python_mart_exactly(cleaned_dataset) -> None:
    """The whole point of the SQL layer: one definition, two implementations.

    If these ever diverge, a fill rate quoted from a dashboard would disagree
    with one quoted from a query, and neither would be obviously wrong.
    """
    sql_mart, python_mart = _aligned_marts(cleaned_dataset.tables)

    assert len(sql_mart) == len(python_mart)
    assert set(sql_mart.columns) == set(python_mart.columns)
    for column in MART_KEY:
        assert sql_mart[column].tolist() == python_mart[column].tolist()

    for column in EXACT_MART_COLUMNS:
        left = sql_mart[column].astype(float)
        right = python_mart[column].astype(float)
        assert np.allclose(left.fillna(-1), right.fillna(-1), atol=1e-9), column


def test_sql_and_python_aov_agree_to_the_cent(cleaned_dataset) -> None:
    """SQLite and NumPy break exact halves differently, and nothing else differs.

    NumPy rounds half to even, SQLite rounds half away from zero, so an average
    landing exactly on a half-cent can differ by 0.01 in either direction. The
    calculation is identical; only the display tie-break is not, so parity is
    asserted to the cent rather than to the bit.
    """
    sql_mart, python_mart = _aligned_marts(cleaned_dataset.tables)
    difference = (sql_mart["aov"].astype(float) - python_mart["aov"].astype(float)).abs()

    # A hair above one cent, because the comparison is itself floating point.
    tolerance = 0.01 + 1e-9
    assert difference.max() <= tolerance
    assert difference.fillna(0).le(tolerance).all()


def test_sql_mart_reconciles_back_to_the_source_facts(cleaned_dataset) -> None:
    """The mart must not lose or invent volume."""
    tables = cleaned_dataset.tables
    conn = build_database(tables)
    totals = query(
        conn,
        """
        SELECT ROUND(SUM(net_sales), 2) AS net_sales,
               SUM(sales_units)         AS sales_units,
               SUM(ordered_units)       AS ordered_units,
               SUM(fulfilled_units)     AS fulfilled_units
        FROM mart_commercial_daily
        """,
    ).iloc[0]

    assert totals["net_sales"] == pytest.approx(tables["fact_sales"]["net_sales"].sum(), abs=0.05)
    assert totals["sales_units"] == tables["fact_sales"]["units"].sum()
    assert totals["ordered_units"] == tables["fact_orders"]["ordered_units"].sum()
    assert totals["fulfilled_units"] == tables["fact_orders"]["fulfilled_units"].sum()


# --------------------------------------------------------------------------
# Staging views
# --------------------------------------------------------------------------


def test_staging_views_preserve_the_fact_grain(cleaned_dataset, database) -> None:
    """Enrichment joins are many-to-one, so no view may multiply its fact."""
    tables = cleaned_dataset.tables
    for view, fact in (
        ("stg_sales", "fact_sales"),
        ("stg_orders", "fact_orders"),
        ("stg_inventory", "fact_inventory"),
    ):
        rows = query(database, f"SELECT COUNT(*) AS n FROM {view}").iloc[0]["n"]
        assert rows == len(tables[fact]), view


def test_order_staging_resolves_the_manager_through_the_customer(database) -> None:
    """A manager owns customers, so kam_id cannot come from the order line."""
    mismatched = query(
        database,
        """
        SELECT COUNT(*) AS n
        FROM stg_orders AS o
        JOIN dim_customer AS c ON c.customer_id = o.customer_id
        WHERE o.kam_id <> c.kam_id
        """,
    ).iloc[0]["n"]
    assert mismatched == 0


def test_sales_staging_derives_gross_and_discount_consistently(database) -> None:
    inconsistent = query(
        database,
        """
        SELECT COUNT(*) AS n FROM stg_sales
        WHERE ABS(gross_sales - units * unit_price) > 0.005
           OR ABS(discount_value - (gross_sales - net_sales)) > 0.005
        """,
    ).iloc[0]["n"]
    assert inconsistent == 0


# --------------------------------------------------------------------------
# Analytical queries
# --------------------------------------------------------------------------


def test_every_analytical_query_runs_and_returns_rows(database) -> None:
    """SQL in this repository is executed, not merely documented."""
    names = available_queries()
    assert len(names) >= 7
    for name in names:
        result = run_query_file(database, name)
        assert not result.empty, name


def test_unknown_query_names_are_rejected(database) -> None:
    with pytest.raises(ValueError, match="No query named"):
        run_query_file(database, "does_not_exist")


def test_sql_week_boundaries_match_the_python_period_layer(cleaned_dataset, database) -> None:
    """Both must call the same seven days a week, or figures will not tie out."""
    sql_weeks = set(
        query(database, "SELECT DISTINCT week_start FROM (%s)" % (
            "SELECT DATE(date, 'weekday 0', '-6 days') AS week_start FROM mart_commercial_daily"
        ))["week_start"]
    )
    python_weeks = set(
        add_period_column(
            pd.DataFrame({"date": pd.to_datetime(cleaned_dataset.tables["fact_sales"]["date"])}),
            "week",
        )["period_start"]
        .dt.strftime("%Y-%m-%d")
        .unique()
    )
    assert sql_weeks == python_weeks


def test_daily_tracker_counts_each_order_once(cleaned_dataset, database) -> None:
    """A multi-SKU order is one order, in SQL as in Python."""
    tracker = run_query_file(database, "daily_sales_tracker")
    orders = cleaned_dataset.tables["fact_orders"]

    assert len(tracker) == 60
    assert tracker["orders"].sum() == orders["order_id"].nunique()


def test_kam_scorecard_covers_every_manager_with_a_quota(cleaned_dataset, database) -> None:
    scorecard = run_query_file(database, "kam_scorecard")
    tables = cleaned_dataset.tables

    assert set(scorecard["kam_id"]) == set(tables["dim_kam"]["kam_id"])
    assert scorecard["sales_attainment"].notna().all()
    assert scorecard["net_sales"].sum() == pytest.approx(
        tables["fact_sales"]["net_sales"].sum(), abs=0.05
    )


def test_primary_and_secondary_mix_sums_to_one(database) -> None:
    mix = run_query_file(database, "primary_secondary_mix")
    total = mix["primary_mix"] + mix["secondary_mix"]
    assert np.allclose(total.dropna(), 1.0, atol=1e-3)


def test_weekly_movers_contributions_reconstruct_the_total(database) -> None:
    """Contributions must add back to the movement, as in the Python layer."""
    movers = run_query_file(database, "weekly_movers")
    for week, group in movers.groupby("week_start"):
        assert group["share_of_absolute_movement"].sum() == pytest.approx(1.0, abs=1e-3), week


# --------------------------------------------------------------------------
# Cross-validation against the Python engine
# --------------------------------------------------------------------------


def test_sql_watchlist_finds_the_same_segments_as_the_signal_engine(cleaned_dataset, database) -> None:
    """Two independent implementations must reach the same answer.

    The SQL watchlist and the Python RootSignal engine share no code: one is a
    windowed query over the mart, the other a decomposition with evidence
    gathering. Agreeing on the disrupted segments is real corroboration that the
    business logic, not one implementation of it, produces the result.
    """
    watchlist = run_query_file(database, "supply_watchlist")
    disruption_week = watchlist[watchlist["week_start"] == "2026-02-16"]
    sql_segments = {
        f"{row.region_code} | {row.category}" for row in disruption_week.itertuples()
    }

    signals = detect_signals(
        cleaned_dataset.tables,
        metric="fill_rate",
        dimension=["region_code", "category"],
        period="week",
        current_period="2026-02-16",
        comparison_period="2026-02-09",
        top_n=2,
    )
    python_segments = {signal.segment for signal in signals}

    assert python_segments <= sql_segments
    assert python_segments == {"BLR | Fruits", "BLR | Vegetables"}


def test_watchlist_requires_demand_to_have_held_up(database) -> None:
    """A segment fulfilling less of a shrinking order book is not a supply story."""
    watchlist = run_query_file(database, "supply_watchlist")
    assert (watchlist["demand_change_pct"] >= -0.05).all()
    assert (watchlist["fill_rate_change"] <= -0.05).all()


def test_watchlist_reports_partial_week_coverage(database) -> None:
    """A short week must be visible rather than silently compared to a full one."""
    watchlist = run_query_file(database, "supply_watchlist")
    assert "days_observed" in watchlist.columns
    assert watchlist["days_observed"].between(1, 7).all()


# --------------------------------------------------------------------------
# Multi-SKU orders
#
# The generated data puts one SKU on every order, so COUNT(order_id) and
# COUNT(DISTINCT order_id) agree on it and a distinct-count regression in the
# mart would pass unnoticed. These tests supply an order that spans two SKUs so
# the protection is actually exercised.
# --------------------------------------------------------------------------


def multi_sku_tables() -> dict[str, pd.DataFrame]:
    """A minimal schema-valid dataset whose single order carries two SKUs."""
    date = "2026-01-01"
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
        # One order, two SKU lines.
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
            {
                "date": [date],
                "sku_id": ["S1"],
                "warehouse": ["WH-BLR"],
                "region_code": ["BLR"],
                "opening_stock": [10],
                "received_units": [0],
                "available_stock": [8],
                "ordered_units": [4],
                "fulfilled_units": [2],
                "stockout_flag": [0],
            }
        ),
        "fact_targets": pd.DataFrame(
            {
                "date": [date],
                "region_code": ["BLR"],
                "category": ["Fruits"],
                "channel": ["Modern Trade"],
                "sales_target": [500.0],
                "order_target": [1],
                "fill_rate_target": [0.93],
            }
        ),
        "fact_kam_targets": pd.DataFrame(
            {"date": [date], "kam_id": ["K1"], "sales_target": [500.0], "order_target": [1]}
        ),
    }


def test_sql_mart_counts_a_multi_sku_order_once() -> None:
    """Two SKU lines on one order are one order, not two.

    Replacing COUNT(DISTINCT order_id) with COUNT(order_id) in the mart makes
    this fail; on the generated data, where every order has a single SKU, it
    would not.
    """
    conn = build_database(multi_sku_tables())
    row = query(conn, "SELECT * FROM mart_commercial_daily").iloc[0]

    assert row["order_count"] == 1
    assert row["sales_order_count"] == 1
    assert row["sales_units"] == 5
    assert row["net_sales"] == 560.0
    assert row["ordered_units"] == 7
    assert row["fulfilled_units"] == 5
    assert row["aov"] == 560.0  # net sales over one order, not two


def test_sql_and_python_agree_on_a_multi_sku_order() -> None:
    """Parity must hold on the case the generated data cannot express."""
    tables = multi_sku_tables()
    sql_mart, python_mart = _aligned_marts(tables)

    assert len(sql_mart) == len(python_mart) == 1
    for column in EXACT_MART_COLUMNS:
        assert float(sql_mart[column].iloc[0]) == pytest.approx(
            float(python_mart[column].iloc[0])
        ), column


def test_daily_tracker_counts_a_multi_sku_order_once() -> None:
    conn = build_database(multi_sku_tables())
    tracker = run_query_file(conn, "daily_sales_tracker")
    assert tracker["orders"].sum() == 1
