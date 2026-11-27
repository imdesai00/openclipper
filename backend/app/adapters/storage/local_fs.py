from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.adapters.storage.keys import validate_key
from app.ports.storage import InvalidKeyError, ObjectStat, StorageError

CHUNK = 1024 * 1024


class LocalFilesystemStorage:
    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise InvalidKeyError(f"key escapes storage root: {key!r}")
        return p

    def put_file(self, src: Path, key: str, *, move: bool = False) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            if move:
                try:
                    os.replace(src, tmp)
                except OSError:  # different filesystem
                    shutil.move(str(src), tmp)
            else:
                shutil.copyfile(src, tmp)
            os.replace(tmp, dest)  # atomic: readers never see a half-written object
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"could not store {key}: {exc}") from exc

    def open_read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        path = self._path(key)
        try:
            f = path.open("rb")
        except FileNotFoundError as exc:
            raise StorageError(f"missing object {key}") from exc
        with f:
            f.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                data = f.read(CHUNK if remaining is None else min(CHUNK, remaining))
                if not data:
                    return
                if remaining is not None:
                    remaining -= len(data)
                yield data

    def stat(self, key: str) -> ObjectStat | None:
        try:
            return ObjectStat(size=self._path(key).stat().st_size)
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        parent = path.parent  # tidy empty directories up to (not including) the root
        while parent != self.root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def list_prefix(self, prefix: str) -> Iterator[str]:
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.name.endswith(".part"):
                yield p.relative_to(self.root).as_posix()

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"missing object {key}")
        yield path
