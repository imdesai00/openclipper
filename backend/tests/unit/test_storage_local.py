from pathlib import Path

import pytest

from app.adapters.storage.keys import new_key, validate_key
from app.adapters.storage.local_fs import LocalFilesystemStorage
from app.ports.storage import InvalidKeyError, StorageError

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path: Path) -> LocalFilesystemStorage:
    return LocalFilesystemStorage(tmp_path / "root")


def _file(tmp_path: Path, data: bytes) -> Path:
    p = tmp_path / "src.bin"
    p.write_bytes(data)
    return p


def test_put_read_stat_delete_roundtrip(store: LocalFilesystemStorage, tmp_path: Path) -> None:
    data = bytes(range(256)) * 1000
    store.put_file(_file(tmp_path, data), "p/original/a.bin")
    assert store.stat("p/original/a.bin").size == len(data)  # type: ignore[union-attr]
    assert b"".join(store.open_read("p/original/a.bin")) == data
    store.delete("p/original/a.bin")
    assert store.stat("p/original/a.bin") is None
    store.delete("p/original/a.bin")  # idempotent
    assert not (store.root / "p").exists()  # empty directories are tidied


def test_range_reads_are_inclusive_and_exact(store: LocalFilesystemStorage, tmp_path: Path) -> None:
    data = bytes(range(256)) * 10
    store.put_file(_file(tmp_path, data), "p/x/a.bin")
    assert b"".join(store.open_read("p/x/a.bin", 10, 19)) == data[10:20]
    assert b"".join(store.open_read("p/x/a.bin", 2500, None)) == data[2500:]
    assert b"".join(store.open_read("p/x/a.bin", 5, 5)) == data[5:6]


def test_move_consumes_source_and_copy_keeps_it(store: LocalFilesystemStorage, tmp_path: Path) -> None:
    src = _file(tmp_path, b"abc")
    store.put_file(src, "p/x/copy.bin")
    assert src.exists()
    store.put_file(src, "p/x/moved.bin", move=True)
    assert not src.exists()


def test_list_prefix_ignores_partial_files(store: LocalFilesystemStorage, tmp_path: Path) -> None:
    store.put_file(_file(tmp_path, b"1"), "p/a/1.bin")
    store.put_file(_file(tmp_path, b"2"), "p/b/2.bin")
    (store.root / "p" / "a" / "half.bin.part").write_bytes(b"x")
    assert sorted(store.list_prefix("p")) == ["p/a/1.bin", "p/b/2.bin"]


def test_local_path_gives_a_readable_file(store: LocalFilesystemStorage, tmp_path: Path) -> None:
    store.put_file(_file(tmp_path, b"hello"), "p/a/h.bin")
    with store.local_path("p/a/h.bin") as p:
        assert p.read_bytes() == b"hello"
    with pytest.raises(StorageError), store.local_path("p/a/missing.bin"):
        pass


@pytest.mark.parametrize(
    "key",
    ["../etc/passwd", "/abs/path", "a/../../b", "a//b", "a/b/", ".hidden", "a/b\\c", "a/b c", "", "x" * 600],
)
def test_hostile_keys_are_rejected(store: LocalFilesystemStorage, key: str) -> None:
    with pytest.raises(InvalidKeyError):
        validate_key(key)
    with pytest.raises(InvalidKeyError):
        store.stat(key)


def test_generated_keys_are_random_and_ignore_hostile_extensions() -> None:
    import uuid

    pid = uuid.uuid4()
    a, b = new_key(pid, "original", "mp4"), new_key(pid, "original", "mp4")
    assert a != b and a.startswith(f"{pid}/original/") and a.endswith(".mp4")
    k = new_key(pid, "original", "../../x.sh;rm")
    validate_key(k)
    assert ".." not in k and ";" not in k
