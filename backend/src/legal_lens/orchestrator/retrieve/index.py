"""The passage index: embedded Qdrant, one point per passage, two vectors each.

Two properties are worth knowing before reading the code.

**Both vectors live on one point.** A passage's dense and sparse representations
update atomically, so there is no window in which the lexical arm can find a chunk the
semantic arm has already dropped — which is what a two-collection design would
produce on every re-index.

**Fusion happens in the store.** The collection declares a sparse vector with
`Modifier.IDF`, so Qdrant computes the IDF half of BM25 against the live corpus, and
`FusionQuery(fusion=Fusion.RRF)` merges the two arms server-side. The application-layer
reciprocal-rank fusion in `retrieval/select.py` is not carried over, because ranks
computed from two separate round trips cannot see each other's corpus statistics.

Embedded local mode — `QdrantClient(path=...)` — needs no server and no Docker. Set
`ORCH_QDRANT_URL` to point at a running instance instead; nothing else changes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ..config import OrchestratorConfig
from ..types import Passage
from .encode import Encoders

log = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "bm25"

#: A stable namespace, so re-indexing the same passage overwrites its point rather
#: than adding a second copy of it. Qdrant requires a UUID or an integer id, and
#: chunk ids are neither.
_NAMESPACE = uuid.UUID("6f9b5f4e-0f1a-4a4c-9e2a-1d5c7b3e8a10")


def point_id(doc_id: str, page_start: int, page_end: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{doc_id}:{page_start}:{page_end}"))


class PassageIndex:
    """Indexes and searches `Passage` values. Owns no chunker.

    The unit is whatever the caller supplies — eval-corpus pages from the CLI,
    `chunk_metadata` rows at integration time. Re-deriving chunk boundaries here would
    let this index and the service's stored chunks disagree about what a citation
    points at.
    """

    def __init__(self, config: OrchestratorConfig, encoders: Encoders) -> None:
        from qdrant_client import QdrantClient

        self._config = config
        self._encoders = encoders
        self._collection = config.qdrant_collection

        if config.qdrant_url:
            self._client = QdrantClient(url=config.qdrant_url, timeout=int(config.timeout))
            log.info("qdrant: server", extra={"url": config.qdrant_url})
        else:
            config.qdrant_path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(config.qdrant_path))
            log.info("qdrant: embedded", extra={"path": str(config.qdrant_path)})

        self._ensure_collection()

    # ------------------------------------------------------------------ setup

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                DENSE: models.VectorParams(
                    size=self._encoders.dense.dimensions,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                # IDF is computed by Qdrant against the live corpus. Computing it
                # client-side would freeze document frequencies at whatever the corpus
                # looked like when the encoder was built.
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        log.info(
            "collection created",
            extra={
                "collection": self._collection,
                "dimensions": self._encoders.dense.dimensions,
            },
        )

    def drop(self) -> None:
        """Delete and recreate the collection. Used by `--index` and by tests."""
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)
        self._ensure_collection()

    # ---------------------------------------------------------------- writing

    async def index(self, passages: list[Passage], *, batch_size: int = 64) -> int:
        """Embed and upsert. Returns how many points were written."""
        from qdrant_client import models

        if not passages:
            return 0

        written = 0
        for start in range(0, len(passages), batch_size):
            batch = passages[start : start + batch_size]
            texts = [p.text for p in batch]
            dense, sparse = await asyncio.gather(
                self._encoders.dense.encode(texts),
                self._encoders.sparse.encode(texts),
            )
            points = [
                models.PointStruct(
                    id=point_id(p.doc_id, p.page_start, p.page_end),
                    vector={
                        DENSE: dense[i],
                        SPARSE: models.SparseVector(indices=sparse[i][0], values=sparse[i][1]),
                    },
                    payload={
                        "doc_id": p.doc_id,
                        "owner_id": p.owner_id,
                        "title": p.title,
                        "page_start": p.page_start,
                        "page_end": p.page_end,
                        "text": p.text,
                    },
                )
                for i, p in enumerate(batch)
            ]
            await asyncio.to_thread(
                self._client.upsert, collection_name=self._collection, points=points
            )
            written += len(points)

        log.info("indexed", extra={"passages": written, "collection": self._collection})
        return written

    def remove_document(self, doc_id: str) -> None:
        from qdrant_client import models

        self._client.delete(
            collection_name=self._collection,
            points_selector=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            ),
        )

    def count(self) -> int:
        return int(self._client.count(collection_name=self._collection, exact=True).count)

    # ---------------------------------------------------------------- reading

    def _scope_filter(self, doc_ids: set[str] | None, owner_id: str | None = None) -> Any:
        """Restrict the search to documents in scope, and to one owner.

        The filter travels with the query rather than being applied to its results:
        filtering afterwards would let out-of-scope passages consume the candidate
        budget and silently shrink what the user actually asked about.

        **Two conditions, not one, and the owner is the one that matters.** The
        document filter is what the attorney chose; the owner filter is what the
        service guarantees. Before the payload carried an owner, a question could
        only reach another tenant's pages through a bug in the caller that resolves
        the document set — one correct call site standing between a tenant and
        privileged text that is not theirs. Both conditions are `must`, so an owner
        mismatch cannot be satisfied by a document match.
        """
        from qdrant_client import models

        conditions = []
        if doc_ids:
            conditions.append(
                models.FieldCondition(key="doc_id", match=models.MatchAny(any=sorted(doc_ids)))
            )
        if owner_id:
            conditions.append(
                models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id))
            )
        return models.Filter(must=conditions) if conditions else None

    async def hybrid_search(
        self,
        question: str,
        *,
        doc_ids: set[str] | None = None,
        owner_id: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[Passage, float]]:
        """Dense and BM25 arms, fused by Qdrant with reciprocal-rank fusion.

        Both arms run as `prefetch` branches of a single request, so one round trip
        does the whole thing and the fusion sees both rankings. The returned score is
        the RRF score, which is a rank-blend and not comparable to a cosine — the
        reranker is what produces a score with meaning.
        """
        from qdrant_client import models

        per_arm = limit or self._config.candidates
        dense_vector, sparse_pairs = await asyncio.gather(
            # `encode_query`, not `encode`: an instruction-tuned embedder needs its
            # prefix here and raw text at index time. See DenseEncoder's docstring.
            self._encoders.dense.encode_query(question),
            self._encoders.sparse.encode_query(question),
        )
        query_filter = self._scope_filter(doc_ids, owner_id)

        response = await asyncio.to_thread(
            self._client.query_points,
            collection_name=self._collection,
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using=DENSE,
                    limit=per_arm,
                    filter=query_filter,
                ),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse_pairs[0], values=sparse_pairs[1]),
                    using=SPARSE,
                    limit=per_arm,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=per_arm,
            with_payload=True,
        )

        hits: list[tuple[Passage, float]] = []
        for point in response.points:
            payload = point.payload or {}
            hits.append(
                (
                    Passage(
                        doc_id=payload["doc_id"],
                        title=payload["title"],
                        page_start=int(payload["page_start"]),
                        page_end=int(payload["page_end"]),
                        text=payload["text"],
                        owner_id=payload.get("owner_id", ""),
                    ),
                    float(point.score),
                )
            )
        log.info(
            "hybrid search",
            extra={"question_chars": len(question), "candidates": len(hits), "per_arm": per_arm},
        )
        return hits

    async def all_pages(
        self, doc_ids: set[str] | None = None, owner_id: str | None = None
    ) -> list[Passage]:
        """Every indexed passage in scope, in document and page order.

        This is the summary workflow's retrieval: a summary of retrieved fragments is
        not a summary, and the nine fields include *Open items*, which is an absence
        question by nature.
        """
        records, _ = await asyncio.to_thread(
            self._client.scroll,
            collection_name=self._collection,
            scroll_filter=self._scope_filter(doc_ids, owner_id),
            limit=10_000,
            with_payload=True,
            with_vectors=False,
        )
        passages = [
            Passage(
                doc_id=r.payload["doc_id"],
                title=r.payload["title"],
                page_start=int(r.payload["page_start"]),
                page_end=int(r.payload["page_end"]),
                text=r.payload["text"],
            )
            for r in records
            if r.payload
        ]
        passages.sort(key=lambda p: (p.doc_id, p.page_start, p.page_end))
        return passages

    def close(self) -> None:
        self._client.close()
