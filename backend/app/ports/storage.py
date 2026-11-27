"""Storage port. Domain code depends on this, never on a concrete backend (SPEC §5.1)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    pass


class InvalidKeyError(StorageError):
    pass


@dataclass(frozen=True)
class ObjectStat:
    size: int


class StorageBackend(Protocol):
    """Blocking API. Async callers wrap it in a thread."""

    name: str

    def put_file(self, src: Path, key: str, *, move: bool = False) -> None:
        """Store the local file `src` under `key`. With move=True `src` is consumed."""

    def open_read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """Stream bytes [start, end] inclusive (end=None means to the end)."""

    def stat(self, key: str) -> ObjectStat | None: ...

    def delete(self, key: str) -> None:
        """Idempotent: deleting a missing key is not an error."""

    def list_prefix(self, prefix: str) -> Iterator[str]: ...

    def local_path(self, key: str) -> AbstractContextManager[Path]:
        """A readable local path for tools like ffmpeg (a download for remote backends)."""
