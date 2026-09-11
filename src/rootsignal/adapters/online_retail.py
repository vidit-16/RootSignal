"""Adapt the UCI Online Retail II dataset to the RootSignal business model.

Real transactions from a UK online retailer, December 2009 to December 2011:
roughly a million invoice lines, 40 countries, and the data quality of an
operational system rather than of a tidied example. About a fifth of lines have
no customer, thousands carry negative quantities or non-positive prices, and
several thousand rows are exact duplicates.

The point of running it is to show the pipeline is not built around its own
generator. Nothing in the analytical layers changes to accommodate it; only this
mapping exists.

**It carries sales and nothing else.** There is no record of what was ordered but
never shipped, no stock, and no plan, so fill rate, inventory and target
analysis are declared unavailable rather than approximated. Returns are present
and are genuinely interesting, but a return is a customer sending something back,
not a warehouse failing to ship it. Treating them as unfulfilled demand would
turn a returns problem into a supply signal, which is exactly the kind of quiet
wrongness this project exists to avoid.

Source: https://archive.ics.uci.edu/dataset/502/online+retail+ii
Licence: Creative Commons Attribution 4.0 International (CC BY 4.0).
"""

from __future__ import annotations

import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from .base import AdaptedDataset, DatasetCapabilities, empty_fact

DOWNLOAD_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
ARCHIVE_NAME = "online_retail_ii.zip"
WORKBOOK_NAME = "online_retail_II.xlsx"
DEFAULT_CACHE = Path("data/raw/external/online_retail")

CAPABILITIES = DatasetCapabilities(
    name="UCI Online Retail II (UK online retailer, 2009-2011)",
    available_facts=("fact_sales",),
    notes=(
        "Transactions only. The retailer's order book, stock positions and plan "
        "are not in this dataset, so fulfilment, inventory and target analysis "
        "are unavailable rather than estimated.",
        "Returns appear as negative quantities and as invoices prefixed with C. "
        "They are separated out and reported as returns, never as unfulfilled "
        "demand: a customer sending goods back is not a warehouse failing to "
        "ship them.",
        "Country stands in for region. It is a real dimension in this data, but "
        "it is a market rather than a distribution geography.",
        "Product category is not recorded. A coarse category is derived from the "
        "first word of the product description, which is an approximation and is "
        "labelled as one.",
        "Roughly a fifth of lines have no customer identifier. Those rows are "
        "kept with an explicit UNKNOWN customer rather than dropped, because "
        "discarding a fifth of revenue would misstate the business.",
    ),
)


def download(cache_dir: str | Path = DEFAULT_CACHE, force: bool = False) -> Path:
    """Fetch the dataset archive, reusing a previous download.

    Roughly 44 MB from the UCI Machine Learning Repository. Nothing else in the
    project needs the network, so this is the only place it is used.
    """
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / ARCHIVE_NAME

    if archive.exists() and not force:
        return archive

    urllib.request.urlretrieve(DOWNLOAD_URL, archive)  # noqa: S310 - fixed public URL
    return archive


def read_raw(cache_dir: str | Path = DEFAULT_CACHE, sheets: int | None = None) -> pd.DataFrame:
    """Read the invoice lines exactly as published, with no cleaning.

    Values arrive untouched: validation reports the defects and cleaning decides
    what to do about them, exactly as with any other source.
    """
    archive = download(cache_dir)
    with zipfile.ZipFile(archive) as bundle, bundle.open(WORKBOOK_NAME) as handle:
        workbook = pd.ExcelFile(handle)
        names = workbook.sheet_names[:sheets] if sheets else workbook.sheet_names
        frames = [workbook.parse(name) for name in names]
    return pd.concat(frames, ignore_index=True)


def _derive_category(description: pd.Series) -> pd.Series:
    """A coarse category from the product description's first word.

    The dataset records no category. This is an approximation, declared as one
    in the capabilities, and it exists so that decomposition has a second
    dimension to work with beyond country.
    """
    words = description.fillna("UNKNOWN").astype(str).str.strip().str.upper()
    return words.str.split().str[0].fillna("UNKNOWN").replace("", "UNKNOWN")


def adapt(
    cache_dir: str | Path = DEFAULT_CACHE,
    sheets: int | None = None,
    raw: pd.DataFrame | None = None,
) -> AdaptedDataset:
    """Map invoice lines onto the business model, keeping defects intact.

    Returns are split out of sales rather than netted into them. Netting would
    hide both the sale and the return; keeping them separate lets each be
    counted, and lets the return rate be reported as what it is.
    """
    source = raw if raw is not None else read_raw(cache_dir, sheets)
    source_rows = len(source)

    frame = source.rename(
        columns={
            "Invoice": "order_id",
            "StockCode": "sku_id",
            "Description": "sku_name",
            "Quantity": "units",
            "InvoiceDate": "timestamp",
            "Price": "unit_price",
            "Customer ID": "customer_id",
            "Country": "region_code",
        }
    ).copy()

    frame["date"] = pd.to_datetime(frame["timestamp"], errors="coerce").dt.date
    frame["order_id"] = frame["order_id"].astype(str)
    frame["sku_id"] = frame["sku_id"].astype(str)
    frame["category"] = _derive_category(frame["sku_name"])

    # A fifth of lines have no customer. Dropping them would remove a fifth of
    # the revenue; naming them keeps the total honest and the gap visible.
    frame["customer_id"] = (
        frame["customer_id"].astype("string").str.replace(r"\.0$", "", regex=True).fillna("UNKNOWN")
    )

    # This dataset has no channel or sales type. Rather than invent variety,
    # both are set to a single honest value.
    frame["channel"] = "Online"
    frame["sales_type"] = "PRIMARY"
    frame["kam_id"] = "UNASSIGNED"

    returns = (frame["units"] < 0) | frame["order_id"].str.upper().str.startswith("C")
    sales = frame.loc[~returns].copy()
    returned = frame.loc[returns].copy()

    sales["discount_pct"] = 0.0
    sales["net_sales"] = (sales["units"] * sales["unit_price"]).round(2)

    fact_sales, consolidated = _consolidate_repeated_lines(sales)

    tables = {
        "dim_date": _build_dim_date(fact_sales["date"]),
        "dim_sku": _build_dim_sku(sales),
        "dim_customer": _build_dim_customer(sales),
        "dim_kam": pd.DataFrame({"kam_id": ["UNASSIGNED"], "kam_name": ["Not recorded"]}),
        "dim_region": _build_dim_region(sales),
        "fact_sales": fact_sales,
        "fact_orders": empty_fact("fact_orders"),
        "fact_inventory": empty_fact("fact_inventory"),
        "fact_targets": empty_fact("fact_targets"),
        "fact_kam_targets": empty_fact("fact_kam_targets"),
    }

    notes = CAPABILITIES.notes + (
        f"{len(returned):,} of {source_rows:,} lines are returns and are held "
        "separately from sales.",
        f"{consolidated:,} invoice lines repeat a product already on the same "
        "invoice. A real invoice can list one product twice, so the declared key "
        "of order, product and sales type does not hold here as published. Those "
        "lines are consolidated into one, adding the units and weighting the "
        "price by quantity, which preserves both volume and revenue exactly.",
    )
    return AdaptedDataset(
        tables=tables,
        capabilities=DatasetCapabilities(
            name=CAPABILITIES.name,
            available_facts=CAPABILITIES.available_facts,
            notes=notes,
        ),
        source_rows=source_rows,
    )


def _consolidate_repeated_lines(sales: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Combine lines that repeat a product already on the same invoice.

    The business model keys a sales line on order, product and sales type. That
    holds in generated data, where an order carries each product once, and it
    does not hold here: 12,000 invoices list a product on more than one line,
    usually with a different quantity.

    This is a real property of invoice data rather than a defect, and the honest
    handling is to say so and combine the lines. Units are added and the price is
    weighted by quantity, so both the volume and the revenue are unchanged.
    """
    columns = [
        "order_id", "date", "customer_id", "region_code", "channel", "kam_id",
        "sku_id", "category", "sales_type", "units", "unit_price", "discount_pct", "net_sales",
    ]
    frame = sales[columns]
    key = ["order_id", "sku_id", "sales_type"]

    repeated = int(frame.duplicated(subset=key, keep="first").sum())
    if repeated == 0:
        return frame.reset_index(drop=True), 0

    grouped = frame.groupby(key, as_index=False, sort=False).agg(
        date=("date", "first"),
        customer_id=("customer_id", "first"),
        region_code=("region_code", "first"),
        channel=("channel", "first"),
        kam_id=("kam_id", "first"),
        category=("category", "first"),
        units=("units", "sum"),
        discount_pct=("discount_pct", "first"),
        net_sales=("net_sales", "sum"),
    )
    # Price follows from the combined revenue and volume, so it stays consistent
    # with both rather than being averaged independently of them.
    grouped["unit_price"] = (
        grouped["net_sales"] / grouped["units"].where(grouped["units"] != 0)
    ).round(4)
    return grouped[columns].reset_index(drop=True), repeated


def returns_frame(
    cache_dir: str | Path = DEFAULT_CACHE,
    sheets: int | None = None,
    raw: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The returned lines, reported as returns and named as such."""
    source = raw if raw is not None else read_raw(cache_dir, sheets)
    frame = source.rename(
        columns={"Invoice": "order_id", "Quantity": "units", "InvoiceDate": "timestamp"}
    ).copy()
    frame["order_id"] = frame["order_id"].astype(str)
    mask = (frame["units"] < 0) | frame["order_id"].str.upper().str.startswith("C")
    returned = frame.loc[mask].copy()
    returned["date"] = pd.to_datetime(returned["timestamp"], errors="coerce").dt.date
    returned["returned_units"] = returned["units"].abs()
    return returned


def _build_dim_date(dates: pd.Series) -> pd.DataFrame:
    unique = pd.to_datetime(pd.Series(sorted(set(dates.dropna()))))
    return pd.DataFrame(
        {
            "date": unique.dt.date,
            "week": unique.dt.isocalendar().week.astype(int).to_numpy(),
            "month": unique.dt.month.to_numpy(),
            "quarter": ("Q" + unique.dt.quarter.astype(str)).to_numpy(),
            "year": unique.dt.year.to_numpy(),
        }
    )


def _build_dim_sku(sales: pd.DataFrame) -> pd.DataFrame:
    # Price is taken from lines that carry a real one. Samples and adjustments
    # sit at or below zero in this data and would drag a median negative.
    priced = sales.loc[sales["unit_price"] > 0]
    sku = (
        sales.groupby("sku_id", as_index=False)
        .agg(sku_name=("sku_name", "first"), category=("category", "first"))
        .merge(
            priced.groupby("sku_id", as_index=False).agg(list_price=("unit_price", "median")),
            on="sku_id",
            how="left",
        )
    )
    sku["list_price"] = sku["list_price"].fillna(0.0)
    sku["sku_name"] = sku["sku_name"].fillna("Not recorded").astype(str)
    sku["sub_category"] = "Standard"
    sku["pack_size_kg"] = 1.0
    # Cost is not recorded. A single stated fraction of price is used so the
    # column exists for the schema, and it is not presented as a real margin.
    sku["unit_cost"] = (sku["list_price"] * 0.7).round(4)
    sku["active_flag"] = True
    return sku[
        ["sku_id", "sku_name", "category", "sub_category", "pack_size_kg", "unit_cost", "list_price", "active_flag"]
    ]


def _build_dim_customer(sales: pd.DataFrame) -> pd.DataFrame:
    customer = (
        sales.groupby("customer_id", as_index=False)
        .agg(region_code=("region_code", "first"))
    )
    customer["customer_name"] = customer["customer_id"].map(
        lambda value: "Unidentified customer" if value == "UNKNOWN" else f"Customer {value}"
    )
    customer["customer_type"] = "Retail"
    customer["channel"] = "Online"
    customer["city"] = customer["region_code"]
    customer["zone"] = customer["region_code"]
    customer["kam_id"] = "UNASSIGNED"
    return customer[
        ["customer_id", "customer_name", "customer_type", "channel", "region_code", "city", "zone", "kam_id"]
    ]


def _build_dim_region(sales: pd.DataFrame) -> pd.DataFrame:
    regions = sorted(sales["region_code"].dropna().astype(str).unique())
    return pd.DataFrame({"region_code": regions, "city": regions, "zone": regions})
