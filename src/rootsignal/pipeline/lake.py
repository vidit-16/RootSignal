"""Where the pipeline keeps its files: a local folder, or an S3 bucket.

Both are laid out the same way, one prefix per zone:

    raw/online_retail/       the source as published, plus a CSV Spark can read
    curated/online_retail/   one Parquet dataset per conformed table
    clean/online_retail/     the cleaned model, typed as schema.sql, for Redshift
    jobs/                    the Spark script, for Glue or EMR to fetch

The rest of the pipeline only asks for a zone's URI, puts a file into one, or
fetches one to local disk, so a run on a laptop and a run against S3 differ in
the root they were given and nothing else.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

RAW = "raw/online_retail"
CURATED = "curated/online_retail"
CLEAN = "clean/online_retail"
JOBS = "jobs"


@dataclass(frozen=True)
class Lake:
    root: str

    @property
    def is_s3(self) -> bool:
        return self.root.startswith("s3://")

    @property
    def bucket(self) -> str:
        return self.root.removeprefix("s3://").split("/", 1)[0]

    @property
    def prefix(self) -> str:
        parts = self.root.removeprefix("s3://").split("/", 1)
        return parts[1].strip("/") if len(parts) > 1 else ""

    def key(self, *parts: str) -> str:
        return "/".join(part.strip("/") for part in (self.prefix, *parts) if part)

    def uri(self, *parts: str) -> str:
        """The location Spark reads or writes: s3:// on AWS, a path locally."""
        if self.is_s3:
            return f"s3://{self.bucket}/{self.key(*parts)}"
        return str(Path(self.root, *parts))

    def _client(self):
        import boto3

        return boto3.client("s3")

    def put(self, local: str | Path, *parts: str) -> str:
        """Copy a local file into the lake and return its URI."""
        if self.is_s3:
            self._client().upload_file(str(local), self.bucket, self.key(*parts))
        else:
            target = Path(self.root, *parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if Path(local).resolve() != target.resolve():
                shutil.copyfile(local, target)
        return self.uri(*parts)

    def _objects(self, *parts: str):
        """Keys at exactly this path, or under it as a folder.

        A bare prefix would also match siblings: fact_sales would pick up
        fact_sales_backup.
        """
        base = self.key(*parts)
        paginator = self._client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=base):
            for item in page.get("Contents", []):
                if item["Key"] == base or item["Key"].startswith(base + "/"):
                    yield item["Key"]

    def exists(self, *parts: str) -> bool:
        if not self.is_s3:
            return Path(self.root, *parts).exists()
        return next(self._objects(*parts), None) is not None

    def fetch(self, *parts: str, into: str | Path) -> Path:
        """A local copy of a file or folder. Local lakes are read in place."""
        if not self.is_s3:
            return Path(self.root, *parts)
        client = self._client()
        base = self.key(*parts)
        target = Path(into, *parts)
        found = False
        for key in self._objects(*parts):
            relative = key[len(base):].lstrip("/")
            destination = target / relative if relative else target
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(self.bucket, key, str(destination))
            found = True
        if not found:
            raise FileNotFoundError(f"Nothing in the lake at {self.uri(*parts)}")
        return target
