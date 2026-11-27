from __future__ import annotations

from app.adapters.storage.local_fs import LocalFilesystemStorage
from app.adapters.storage.s3 import S3CompatibleStorage
from app.config import Settings
from app.ports.storage import StorageBackend, StorageError


def build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalFilesystemStorage(settings.storage_local_root)
    missing = [
        n
        for n, v in (
            ("S3_BUCKET", settings.s3_bucket),
            ("S3_ACCESS_KEY", settings.s3_access_key),
            ("S3_SECRET_KEY", settings.s3_secret_key),
        )
        if not v
    ]
    if missing:
        raise StorageError(f"STORAGE_BACKEND=s3 requires {', '.join(missing)}")
    assert settings.s3_bucket and settings.s3_access_key and settings.s3_secret_key
    store = S3CompatibleStorage(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        region=settings.s3_region,
        scratch_dir=settings.scratch_dir,
    )
    store.ensure_bucket()
    return store
