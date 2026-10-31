"""The orchestrator's hybrid index, fed from this service's database.

`orchestrator/retrieve/` knows how to index and search `Passage` values and owns no
chunker, deliberately: re-deriving chunk boundaries there would let its index and the
service's stored chunks disagree about what a citation points at. This module is the
other half of that bargain — it reads `chunk_metadata` and `document_pages` and hands
them over, so both sides index exactly the boundaries `ingestion/chunk.py` produced.

## Why this is lazy

Building the encoders loads two torch models (~2.5GB resident) and takes tens of
seconds. Nothing here is constructed until the first question actually needs it, and
when it is constructed it happens in a worker thread — a model load on the event loop
would stall every other request, including `/health`.

## Owner scoping

Two independent filters, and that is the point. The caller resolves a document set
from `documents.list(owner_id)` and passes it in; `_scoped` requires it to be
non-empty and has no "search everything" mode. **And** the owner travels with the
query into the store, because `Passage.owner_id` is in the Qdrant payload and
`PassageIndex._scope_filter` makes it a `must` condition alongside the document ids.

The second filter is what changed. Before it, a question could reach another tenant's
privileged text through a single bug in the one call site that resolves the document
set — correct today, and the only thing standing in the way. Now a mistake there
yields no results rather than someone else's contract. `PageStore.scoped_to` applies
the same narrowing to the page text, which lives outside Qdrant and had the same
exposure.

## Why a count mismatch re-indexes everything

Qdrant persists across a restart and `PageStore` does not, so boot has to repopulate
one and verify the other. If the two counts disagree — the orchestrator was switched
on over a database that predates it, or `var/` was cleared — the index is rebuilt
whole rather than diffed per document. Diffing is the kind of cleverness that leaves
three documents silently unsearchable, and the symptom of under-indexing is not an
error: it is an assistant that abstains on questions the documents plainly answer.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Settings
from ..orchestrator.config import OrchestratorConfig
from ..orchestrator.types import Passage, Retrieved

if TYPE_CHECKING:  # pragma: no cover - import cost is the whole point of deferring
    from ..db.repositories import DocumentRepository
    from ..db.session import Database
    from ..orchestrator.retrieve import PassageIndex, Retriever

log = logging.getLogger(__name__)


def config_for(settings: Settings) -> OrchestratorConfig:
    """`OrchestratorConfig`, pointed at this service's storage.

    Everything else — models, effort, candidate counts, the reranker switch — stays
    on the `ORCH_` environment variables the CLI reads, so a setting tuned against
    the eval corpus means the same thing in the running service. Only the two values
    that would collide are overridden: embedded Qdrant takes an exclusive lock on its
    directory, so the service cannot share the CLI's.
    """
    return OrchestratorConfig(
        qdrant_path=Path(settings.orchestrator_qdrant_path),
        qdrant_collection=settings.orchestrator_collection,
        api_key=settings.anthropic_api_key,
        workspace_id=settings.anthropic_workspace_id,
    )


class OrchestratedRetrieval:
    """Owns the orchestrator's index and page store for the running service."""

    def __init__(self, db: Database, documents: DocumentRepository, settings: Settings) -> None:
        self._db = db
        self._documents = documents
        self._settings = settings
        self.config = config_for(settings)

        # Built on first use, under the lock. `PageStore` is cheap (a dict) and is
        # populated at boot; the index and the encoders are not.
        from ..orchestrator.retrieve import PageStore

        self.pages = PageStore()
        self._index: PassageIndex | None = None
        self._retriever: Retriever | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ build

    async def retriever(self) -> Retriever:
        """The retriever, building the heavy half on first call.

        Held under a lock because two questions arriving together would otherwise
        each load the models — twice the memory and twice the wait, for one index.
        """
        if self._retriever is not None:
            return self._retriever

        async with self._lock:
            if self._retriever is not None:
                return self._retriever

            from ..orchestrator.retrieve import Encoders, PassageIndex, Retriever

            log.info(
                "loading retrieval models",
                extra={
                    "embed_model": self.config.embed_model,
                    "rerank_model": self.config.rerank_model,
                    "rerank_enabled": self.config.rerank_enabled,
                },
            )
            encoders = await asyncio.to_thread(
                Encoders,
                embed_model=self.config.embed_model,
                rerank_model=self.config.rerank_model,
                device=self.config.device,
                with_reranker=self.config.rerank_enabled,
                query_prompt=self.config.query_prompt,
            )
            await encoders.warm()
            index = await asyncio.to_thread(PassageIndex, self.config, encoders)

            self._index = index
            self._retriever = Retriever(self.config, index, self.pages, encoders)
            log.info("retrieval ready", extra={"points": index.count()})
            return self._retriever

    async def _ensure_index(self) -> PassageIndex:
        """The index, built if it is not yet. The write-side counterpart of
        `retriever()`, which builds exactly the same objects."""
        await self.retriever()
        assert self._index is not None  # set by `retriever()` under the lock
        return self._index

    # ---------------------------------------------------------------- reading

    async def select(
        self,
        question: str,
        *,
        doc_ids: set[str],
        owner_id: str,
        top_n: int | None = None,
    ) -> Retrieved:
        """Whole-document or hybrid, whichever the corpus size calls for.

        What `ask` and `draft` use. See `Retriever.select` for the route decision.
        """
        retriever = await self._scoped(doc_ids, owner_id)
        return await retriever.select(question, doc_ids=doc_ids, owner_id=owner_id, top_n=top_n)

    async def search(
        self,
        question: str,
        *,
        doc_ids: set[str],
        owner_id: str,
        top_n: int | None = None,
    ) -> Retrieved:
        """Hybrid search over exactly `doc_ids`, for exactly `owner_id`."""
        retriever = await self._scoped(doc_ids, owner_id)
        return await retriever.search(question, doc_ids=doc_ids, owner_id=owner_id, top_n=top_n)

    async def whole(self, doc_ids: set[str], owner_id: str) -> Retrieved:
        """Every page in scope, no retrieval. What `summary` reads."""
        retriever = await self._scoped(doc_ids, owner_id)
        return await retriever.whole(doc_ids, owner_id=owner_id)

    async def _scoped(self, doc_ids: set[str], owner_id: str):
        """The retriever, with the scope checked before anything is searched.

        Both arguments are required and non-empty: see the owner-scoping note in the
        module docstring. A caller with nothing in scope should abstain rather than
        ask for a search of everything, which this has no way to express — and an
        owner is what makes the payload filter reachable, so a missing one would
        silently fall back to filtering on documents alone.
        """
        if not doc_ids:
            raise ValueError(
                "retrieval needs an explicit, non-empty document scope; an empty "
                "scope is an abstention, not a search of every document"
            )
        if not owner_id:
            raise ValueError(
                "retrieval needs an owner; without one the index filter falls back "
                "to document ids alone and scoping rests on the caller being right"
            )
        return await self.retriever()

    # ---------------------------------------------------------------- writing

    async def _passages_for(self, document_id: str) -> list[Passage]:
        rows = await self._db.fetch_all(
            """SELECT c.document_id, c.owner_id, d.title, c.page_start, c.page_end, c.text
                 FROM chunk_metadata c
                 JOIN documents d ON d.id = c.document_id
                WHERE c.document_id = ?
                ORDER BY c.ordinal""",
            (document_id,),
        )
        return [
            Passage(
                doc_id=r["document_id"],
                title=r["title"],
                page_start=r["page_start"],
                page_end=r["page_end"],
                text=r["text"],
                # Into the Qdrant payload, so the store can filter on it rather than
                # trusting the caller to have resolved the document set correctly.
                owner_id=r["owner_id"],
            )
            for r in rows
        ]

    async def _load_pages(self, document_id: str) -> None:
        """Put one document's pages in the store, keyed for citation resolution.

        Read without an owner filter on purpose: this is the index-side read, and the
        index holds every owner's documents exactly as the in-memory vector store
        already does. Scoping happens on the way out, in `search`.
        """
        rows = await self._db.fetch_all(
            """SELECT d.title, d.owner_id, p.page, p.text
                 FROM document_pages p
                 JOIN documents d ON d.id = p.document_id
                WHERE p.document_id = ?
                ORDER BY p.page""",
            (document_id,),
        )
        if not rows:
            return
        self.pages.add_document(
            document_id,
            rows[0]["title"],
            [(r["page"], r["text"]) for r in rows],
            owner_id=rows[0]["owner_id"],
        )

    async def index_document(self, document_id: str) -> int:
        """Index one document's chunks and load its pages. Returns points written."""
        await self._load_pages(document_id)
        passages = await self._passages_for(document_id)
        if not passages:
            return 0

        index = await self._ensure_index()
        # Deterministic point ids make an upsert idempotent, but a re-ingested
        # document can have *fewer* chunks than before, and those stale points would
        # keep answering questions from text the user replaced.
        await asyncio.to_thread(index.remove_document, document_id)
        return await index.index(passages)

    def remove_document(self, document_id: str) -> None:
        """Drop a deleted document from both halves (NFR-8).

        A no-op before the index is built, which is correct: nothing is searchable
        yet, and the document is gone from SQLite, so the boot-time reconcile will
        never re-add it.
        """
        if self._index is not None:
            self._index.remove_document(document_id)

    async def prepare(self) -> int:
        """Populate the page store at boot, and reconcile the index against SQLite.

        Returns the number of passages indexed, which is zero in the common case
        where Qdrant already agrees with the database.
        """
        document_ids = [
            r["id"]
            for r in await self._db.fetch_all(
                "SELECT id FROM documents WHERE status = 'ready' ORDER BY id"
            )
        ]
        for document_id in document_ids:
            await self._load_pages(document_id)

        counted = await self._db.fetch_one(
            """SELECT COUNT(*) AS n
                 FROM chunk_metadata c
                 JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'ready'"""
        )
        expected = counted["n"] if counted else 0
        log.info(
            "page store loaded",
            extra={"documents": len(document_ids), "pages": self.pages.count_pages()},
        )
        if not expected:
            return 0

        index = await self._ensure_index()
        actual = index.count()
        if actual == expected:
            log.info("index reconciled", extra={"passages": actual})
            return 0

        # See the module docstring: rebuilt whole rather than diffed.
        log.warning(
            "index does not match the database; rebuilding",
            extra={"indexed": actual, "expected": expected},
        )
        await asyncio.to_thread(index.drop)
        written = 0
        for document_id in document_ids:
            passages = await self._passages_for(document_id)
            written += await index.index(passages)
        log.info("index rebuilt", extra={"passages": written})
        return written

    def status(self) -> str:
        """What `/health` reports for this pipeline.

        Deliberately does not build anything: asking whether the models are loaded
        must not load them, or a health check would cost 30 seconds and 2.5GB.

        Zero passages against a non-empty page store is the shape of the silent
        failure this whole path risks — retrieval returns nothing, every question
        abstains, and nothing anywhere is an error. So it reads as degraded.
        """
        pages = self.pages.count_pages()
        if self._index is None:
            return f"ok (idle, {pages} pages loaded; models load on first question)"
        passages = self._index.count()
        if pages and not passages:
            return f"{pages} pages loaded but nothing indexed: retrieval will find nothing"
        return f"ok ({passages} passages, {pages} pages)"

    def close(self) -> None:
        if self._index is not None:
            self._index.close()
