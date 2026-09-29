"""File storage abstraction.

Independent of the backend/FastAPI layer by design (see docs/ARCHITECTURE.md) —
the backend imports this module, never the reverse. `LocalStorageBackend` is
the only implementation for now (a Docker-mounted directory that survives
container restarts/recreation); an S3-compatible backend can be added later
behind the same `StorageBackend` interface without touching calling code.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from storage.exceptions import StorageError


class StorageBackend(ABC):
    @abstractmethod
    def open_writer(self, key: str) -> BinaryIO:
        """Open a binary file handle for writing at `key`, creating parent dirs."""

    @abstractmethod
    def absolute_path(self, key: str) -> Path:
        """Resolve `key` to an absolute filesystem path, for reading/serving."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete the file at `key`. No-op if it doesn't exist."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Recursively delete everything under `prefix`. No-op if it doesn't exist."""


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise StorageError(f"Invalid storage key: {key!r}")
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StorageError(f"Storage key escapes storage root: {key!r}")
        return candidate

    def open_writer(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "wb")

    def absolute_path(self, key: str) -> Path:
        return self._resolve(key)

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.is_file():
            path.unlink()

    def delete_prefix(self, prefix: str) -> None:
        path = self._resolve(prefix)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
