"""Vector stores.

The in-memory store is process-local and holds no state across a restart, which is
why `Services.bootstrap` rebuilds it from `chunk_metadata` at boot. That rebuild is
the thing that keeps `indexed = 1` in SQLite from being a claim about an index that
does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Indexed:
    chunk_id: str
    document_id: str
    matter_id: str
    owner_id: str
    page_start: int
    page_end: int
    text: str
    vector: list[float]


@dataclass(frozen=True)
class Hit:
    chunk: Indexed
    score: float


class VectorStore(Protocol):
    name: str

    def upsert(self, records: list[Indexed]) -> None: ...
    def remove_document(self, document_id: str) -> int: ...
    def search(
        self, vector: list[float], owner_id: str, document_ids: set[str], limit: int
    ) -> list[Hit]: ...
    def count(self) -> int: ...


@dataclass
class MemoryVectorStore:
    """Exhaustive cosine scan.

    At NFR-4's scale — twenty users, a few hundred documents — a linear scan over a
    few thousand chunks is microseconds, and an approximate index would add a
    dependency and a recall question in exchange for nothing measurable.
    """

    name: str = "memory"
    _by_document: dict[str, list[Indexed]] = field(default_factory=dict)

    def upsert(self, records: list[Indexed]) -> None:
        for record in records:
            self._by_document.setdefault(record.document_id, []).append(record)

    def remove_document(self, document_id: str) -> int:
        removed = self._by_document.pop(document_id, [])
        return len(removed)

    def search(
        self, vector: list[float], owner_id: str, document_ids: set[str], limit: int
    ) -> list[Hit]:
        hits: list[Hit] = []
        for document_id in document_ids:
            for chunk in self._by_document.get(document_id, ()):
                # Scoped here as well as by the caller's document set: NFR-11 allows
                # this store to be swapped for Qdrant, where the filter travels with
                # the query, and the two have to mean the same thing.
                if chunk.owner_id != owner_id:
                    continue
                score = sum(a * b for a, b in zip(vector, chunk.vector, strict=False))
                if score > 0:
                    hits.append(Hit(chunk=chunk, score=score))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:limit]

    def count(self) -> int:
        return sum(len(v) for v in self._by_document.values())
