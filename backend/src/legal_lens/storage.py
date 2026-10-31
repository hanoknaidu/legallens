"""The object store: original bytes, kept so the viewer can serve the real file.

A filesystem directory under `data_dir`, which is the whole Phase 1 implementation and
is deliberately behind a small interface — NFR-11's swappable stores include this one,
and S3 or similar is a change to this file alone.

Deletion is recorded rather than assumed (NFR-8): `remove` reports whether the object
was actually gone afterwards, because "we deleted it" is a claim that needs a check.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class ObjectStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        # Keys are generated internally from document ids, never from user input, but
        # a traversal check costs nothing and the alternative is a path escape.
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise ValueError(f"object key escapes the store: {key!r}")
        return candidate

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def path_for(self, key: str) -> Path | None:
        """A real path, so a file response can stream rather than buffer 50MB."""
        path = self._path(key)
        return path if path.is_file() else None

    def remove(self, key: str) -> bool:
        """True when the object is gone afterwards — including if it never existed."""
        try:
            self._path(key).unlink(missing_ok=True)
            return True
        except OSError as exc:
            log.warning("object delete failed", extra={"key": key, "error": str(exc)})
            return False
