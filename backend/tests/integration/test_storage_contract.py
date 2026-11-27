"""One behavioural contract, run against every storage backend. The S3 variant needs a real S3-compatible
server (SeaweedFS in CI); without TEST_S3_ENDPOINT it is reported SKIPPED, never passed."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.adapters.storage.local_fs import LocalFilesystemStorage
from app.adapters.storage.s3 import S3CompatibleStorage
from app.ports.storage import InvalidKeyError, StorageBackend, StorageError

pytestmark = pytest.mark.integration


def _backends() -> list[object]:
    return [
        "local",
        pytest.param(
            "s3",
            marks=pytest.mark.skipif(
                not os.environ.get("TEST_S3_ENDPOINT"),
                reason="SKIPPED: no S3-compatible server (set TEST_S3_ENDPOINT, e.g. SeaweedFS)",
            ),
        ),
    ]


@pytest.fixture(scope="module")
def _s3_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S3CompatibleStorage | None]:
    """One bucket for the whole module (S3 servers allocate resources per bucket) that is removed afterwards."""
    if not os.environ.get("TEST_S3_ENDPOINT"):
        yield None
        return
    s = S3CompatibleStorage(
        bucket=f"oc-test-{uuid.uuid4().hex[:10]}",
        endpoint_url=os.environ["TEST_S3_ENDPOINT"],
        access_key=os.environ.get("TEST_S3_ACCESS_KEY", "test"),
        secret_key=os.environ.get("TEST_S3_SECRET_KEY", "test-secret"),
        scratch_dir=tmp_path_factory.mktemp("s3-scratch"),
    )
    s.ensure_bucket()
    yield s
    for k in list(s.list_prefix("")):
        s.delete(k)
    s._c.delete_bucket(Bucket=s.bucket)


@pytest.fixture(params=_backends())
def store(
    request: pytest.FixtureRequest, tmp_path: Path, _s3_store: S3CompatibleStorage | None
) -> Iterator[StorageBackend]:
    if request.param == "local":
        yield LocalFilesystemStorage(tmp_path / "root")
        return
    assert _s3_store is not None
    yield _s3_store
    for k in list(_s3_store.list_prefix("")):  # every test starts with an empty bucket
        _s3_store.delete(k)


def _src(tmp_path: Path, data: bytes, name: str = "src.bin") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_roundtrip_stat_delete(store: StorageBackend, tmp_path: Path) -> None:
    data = os.urandom(300_000)
    store.put_file(_src(tmp_path, data), "proj/original/a.bin")
    st = store.stat("proj/original/a.bin")
    assert st is not None and st.size == len(data)
    assert b"".join(store.open_read("proj/original/a.bin")) == data
    store.delete("proj/original/a.bin")
    assert store.stat("proj/original/a.bin") is None
    store.delete("proj/original/a.bin")  # idempotent
    assert store.stat("never/existed/x.bin") is None


def test_inclusive_byte_ranges(store: StorageBackend, tmp_path: Path) -> None:
    data = bytes(range(256)) * 40
    store.put_file(_src(tmp_path, data), "proj/x/r.bin")
    assert b"".join(store.open_read("proj/x/r.bin", 10, 19)) == data[10:20]
    assert b"".join(store.open_read("proj/x/r.bin", 10_000)) == data[10_000:]
    assert b"".join(store.open_read("proj/x/r.bin", 0, 0)) == data[:1]


def test_move_semantics(store: StorageBackend, tmp_path: Path) -> None:
    keep = _src(tmp_path, b"copy me", "keep.bin")
    store.put_file(keep, "proj/x/copy.bin")
    assert keep.exists()
    gone = _src(tmp_path, b"move me", "gone.bin")
    store.put_file(gone, "proj/x/moved.bin", move=True)
    assert not gone.exists() and store.stat("proj/x/moved.bin") is not None


def test_overwrite_replaces_content(store: StorageBackend, tmp_path: Path) -> None:
    store.put_file(_src(tmp_path, b"one"), "proj/x/o.bin")
    store.put_file(_src(tmp_path, b"second version"), "proj/x/o.bin")
    assert b"".join(store.open_read("proj/x/o.bin")) == b"second version"


def test_list_prefix(store: StorageBackend, tmp_path: Path) -> None:
    for k in ("p1/a/1.bin", "p1/b/2.bin", "p2/a/3.bin"):
        store.put_file(_src(tmp_path, b"x"), k)
    assert sorted(store.list_prefix("p1/")) == ["p1/a/1.bin", "p1/b/2.bin"]
    assert sorted(store.list_prefix("")) == ["p1/a/1.bin", "p1/b/2.bin", "p2/a/3.bin"]


def test_local_path_gives_a_readable_file_and_cleans_up(store: StorageBackend, tmp_path: Path) -> None:
    store.put_file(_src(tmp_path, b"hello world"), "p/x/h.bin")
    with store.local_path("p/x/h.bin") as p:
        assert p.read_bytes() == b"hello world"
        seen = p
    if store.name == "s3":
        assert not seen.exists()  # a downloaded temp copy is removed afterwards
    with pytest.raises(StorageError), store.local_path("p/x/missing.bin"):
        pass


@pytest.mark.parametrize("key", ["../x", "/abs", "a//b", "a/../b", "", "a b"])
def test_hostile_keys_rejected_by_every_backend(store: StorageBackend, key: str, tmp_path: Path) -> None:
    with pytest.raises(InvalidKeyError):
        store.put_file(_src(tmp_path, b"x"), key)
    with pytest.raises(InvalidKeyError):
        store.stat(key)


def test_large_object_uses_multipart_and_survives_intact(store: StorageBackend, tmp_path: Path) -> None:
    if store.name != "s3":
        pytest.skip("multipart is specific to S3")
    big = tmp_path / "big.bin"
    with big.open("wb") as f:  # 70 MB > the 64 MB multipart threshold
        for _ in range(70):
            f.write(os.urandom(1024 * 1024))
    store.put_file(big, "p/x/big.bin")
    st = store.stat("p/x/big.bin")
    assert st is not None and st.size == big.stat().st_size
    assert b"".join(store.open_read("p/x/big.bin", 66_000_000, 66_000_099)) == big.read_bytes()[66_000_000:66_000_100]
