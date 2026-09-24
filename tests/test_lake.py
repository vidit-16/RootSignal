"""The lake behaves the same on a local folder and on S3.

S3 is simulated with moto, so these run without an AWS account.
"""

from __future__ import annotations

import pytest

from rootsignal.pipeline import CURATED, RAW, Lake


def test_a_local_lake_is_a_folder(tmp_path) -> None:
    lake = Lake(str(tmp_path))
    source = tmp_path / "source.csv"
    source.write_text("a,b\n1,2\n")

    uri = lake.put(source, RAW, "invoices.csv")

    assert uri == str(tmp_path / RAW / "invoices.csv")
    assert lake.exists(RAW, "invoices.csv")
    assert lake.fetch(RAW, "invoices.csv", into=tmp_path / "unused").read_text() == "a,b\n1,2\n"


def test_an_s3_lake_splits_bucket_and_prefix() -> None:
    lake = Lake("s3://rootsignal-lake/project/")
    assert lake.bucket == "rootsignal-lake"
    assert lake.uri(CURATED, "fact_sales") == "s3://rootsignal-lake/project/curated/online_retail/fact_sales"
    assert Lake("s3://rootsignal-lake").uri(RAW) == "s3://rootsignal-lake/raw/online_retail"


@pytest.fixture
def s3(monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3

    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "ap-south-1",
    }.items():
        monkeypatch.setenv(name, value)
    with moto.mock_aws():
        client = boto3.client("s3")
        client.create_bucket(
            Bucket="rootsignal-lake", CreateBucketConfiguration={"LocationConstraint": "ap-south-1"}
        )
        yield client


def test_an_s3_lake_round_trips_a_prefix(s3, tmp_path) -> None:
    lake = Lake("s3://rootsignal-lake/project")
    for part in ("part-0000.parquet", "part-0001.parquet"):
        source = tmp_path / part
        source.write_bytes(part.encode())
        lake.put(source, CURATED, "fact_sales", part)

    assert lake.exists(CURATED, "fact_sales")
    assert not lake.exists(CURATED, "dim_sku")

    fetched = lake.fetch(CURATED, "fact_sales", into=tmp_path / "copy")
    assert sorted(path.name for path in fetched.iterdir()) == ["part-0000.parquet", "part-0001.parquet"]
    assert (fetched / "part-0001.parquet").read_bytes() == b"part-0001.parquet"


def test_a_folder_does_not_pick_up_a_sibling_sharing_its_name(s3, tmp_path) -> None:
    lake = Lake("s3://rootsignal-lake")
    source = tmp_path / "part-0000.parquet"
    source.write_bytes(b"x")
    lake.put(source, CURATED, "fact_sales", "part-0000.parquet")
    lake.put(source, CURATED, "fact_sales_backup", "part-0000.parquet")

    fetched = lake.fetch(CURATED, "fact_sales", into=tmp_path / "copy")

    assert [path.name for path in fetched.iterdir()] == ["part-0000.parquet"]
    assert not (tmp_path / "copy" / CURATED / "fact_sales_backup").exists()


def test_fetching_nothing_from_s3_is_an_error_not_an_empty_folder(s3, tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="curated/online_retail/returns"):
        Lake("s3://rootsignal-lake").fetch(CURATED, "returns", into=tmp_path)
