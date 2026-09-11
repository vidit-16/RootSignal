from pathlib import Path
import subprocess

from rootsignal.cleaning import clean_dataset
from rootsignal.ingestion import load_dataset
from rootsignal.modeling.consolidation import build_commercial_mart
from rootsignal.validation import DatasetValidator

ROOT = Path(__file__).resolve().parents[1]


def test_generated_data_flows_through_rootsignal_pipeline(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "generate_sample_data.py"),
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        cwd=ROOT,
    )

    raw_tables = load_dataset(tmp_path)
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

    expected_sales_orders = clean_tables["fact_sales"].groupby("order_id").size().index.nunique()
    assert int(mart["sales_order_count"].sum()) <= expected_sales_orders
    assert not cleaning_result.audit.empty
    assert len(cleaning_result.quarantined["fact_orders"]) == 1
