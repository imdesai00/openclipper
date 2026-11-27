from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.adapters.storage.keys import validate_key
from app.ports.storage import ObjectStat, StorageError

CHUNK = 1024 * 1024


class S3CompatibleStorage:
    """Works with AWS S3, Cloudflare R2, Backblaze B2, SeaweedFS, Garage, ... (path-style addressing)."""

    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        scratch_dir: str | Path | None = None,
    ) -> None:
        self.bucket = bucket
        self.scratch_dir = Path(scratch_dir) if scratch_dir else None
        self._c: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
                signature_version="s3v4",
            ),
        )
        self._xfer = TransferConfig(multipart_threshold=64 * 1024**2, multipart_chunksize=64 * 1024**2)

    def ensure_bucket(self) -> None:
        try:
            self._c.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self._c.create_bucket(Bucket=self.bucket)
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"bucket {self.bucket} unavailable: {exc}") from exc

    def put_file(self, src: Path, key: str, *, move: bool = False) -> None:
        validate_key(key)
        try:
            self._c.upload_file(str(src), self.bucket, key, Config=self._xfer)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not store {key}: {exc}") from exc
        if move:
            Path(src).unlink(missing_ok=True)

    def open_read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        validate_key(key)
        rng = f"bytes={start}-{'' if end is None else end}"
        try:
            body = self._c.get_object(Bucket=self.bucket, Key=key, Range=rng)["Body"]
        except ClientError as exc:
            raise StorageError(f"could not read {key}: {exc}") from exc
        try:
            yield from body.iter_chunks(CHUNK)
        finally:
            body.close()

    def stat(self, key: str) -> ObjectStat | None:
        validate_key(key)
        try:
            return ObjectStat(size=int(self._c.head_object(Bucket=self.bucket, Key=key)["ContentLength"]))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"could not stat {key}: {exc}") from exc

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self._c.delete_object(Bucket=self.bucket, Key=key)  # S3 delete is idempotent
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not delete {key}: {exc}") from exc

    def list_prefix(self, prefix: str) -> Iterator[str]:
        paginator = self._c.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        validate_key(key)
        with tempfile.TemporaryDirectory(dir=self.scratch_dir) as d:
            dest = Path(d) / Path(key).name
            try:
                self._c.download_file(self.bucket, key, str(dest), Config=self._xfer)
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"could not fetch {key}: {exc}") from exc
            yield dest
