"""The two encoders, and the cross-encoder that reorders what they find.

All three models run **in-process and locally**. That is the BRD §8.2 position as much
as a preference: an embedding vendor is a second processor of privileged
attorney-client text and needs its own data-processing agreement before it is switched
on. An open-weight model on this machine needs none.

Every call into torch is wrapped in `asyncio.to_thread`. A 300 ms encode on the event
loop stalls every other request in the process, including the SSE streams that are the
whole point of streaming an answer.

Imports of `sentence-transformers` and `fastembed` are deferred to construction time,
so this module can be imported — and the rest of the package tested — without the
`workflows` extra installed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: The sparse arm's vocabulary is the whole corpus, so a query term never seen at
#: index time contributes nothing. That is correct BM25 behaviour, not a bug.
_BM25_MODEL = "Qdrant/bm25"


class DenseEncoder:
    """A sentence-transformer, loaded once.

    Loaded eagerly rather than on first use: otherwise the first question after every
    restart pays a multi-second model load, and the symptom ("the first answer is
    slow") is reported as a generation problem rather than a startup one.

    ## Queries and documents are encoded differently, and getting this wrong is silent

    Instruction-tuned embedders — the Qwen3 family, E5, and others — are trained with
    an instruction prefix on the **query** and raw text on the **document**. Encode
    both the same way and the model still returns perfectly plausible vectors; it
    just retrieves worse. Measured on the eval corpus, `Qwen3-Embedding-0.6B` scores
    recall@1 **0.61** with no prefix and **0.67** with it — the whole difference
    between "no better than a 22M-parameter MiniLM" and "the best embedder tested".

    The trap is that `Qwen3-Embedding-0.6B` *ships* the right prompt in its config
    and leaves `default_prompt_name` unset, so sentence-transformers will not apply
    it unless asked. Nothing warns.

    So this class reads the model's own declared `query` prompt and applies it to
    queries only, automatically. A symmetric model like `bge-m3` declares none and is
    unaffected. `query_prompt` overrides the declared one — but measure before using
    it: a hand-written legal instruction scored **0.61** against the model's own
    generic default at **0.67** on this corpus, so a plausible-sounding custom
    instruction is a cost, not a free win.
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        query_prompt: str | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        started = time.perf_counter()
        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        # Renamed in sentence-transformers 6; the old name still works but warns.
        dimension = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dimensions = int(dimension())

        declared = (getattr(self._model, "prompts", None) or {}).get("query") or ""
        self.query_prompt = query_prompt if query_prompt is not None else declared
        log.info(
            "dense encoder loaded",
            extra={
                "model": model_name,
                "dimensions": self.dimensions,
                "max_seq_length": getattr(self._model, "max_seq_length", None),
                # Logged because its absence is invisible otherwise, and its absence
                # on an instruction-tuned model is a measurable quality loss.
                "query_prompt": self.query_prompt or "(none - symmetric model)",
                "query_prompt_source": (
                    "override"
                    if query_prompt is not None
                    else "model config"
                    if declared
                    else "none"
                ),
                "load_ms": int((time.perf_counter() - started) * 1000),
            },
        )

    def _encode(self, texts: list[str]) -> list[list[float]]:
        # Normalised, so a dot product is a cosine and Qdrant's `COSINE` distance and
        # our own arithmetic agree about what "similar" means.
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [[float(v) for v in row] for row in vectors]

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode documents. No instruction prefix — that is the asymmetry."""
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    async def encode_query(self, text: str) -> list[float]:
        """Encode a search query, with the model's instruction prefix if it has one.

        Every query path must come through here rather than `encode`. The two differ
        only for instruction-tuned models, which is exactly what makes mixing them up
        so easy and so quiet.
        """
        prefixed = f"{self.query_prompt} {text}".strip() if self.query_prompt else text
        return (await asyncio.to_thread(self._encode, [prefixed]))[0]

    async def encode_one(self, text: str) -> list[float]:
        """Deprecated alias for `encode_query`.

        Kept so no call site silently falls back to document-style encoding for a
        query during the migration.
        """
        return await self.encode_query(text)


class SparseEncoder:
    """BM25 sparse vectors, tokenised the way Qdrant expects.

    `fastembed`'s Bm25 is used rather than the service's own `lib_text.tokens` for one
    reason that matters: the tokeniser has to agree with itself across indexing and
    querying, and this one is built by Qdrant for exactly this pairing — correct
    stemming and stopwords included. A hand-rolled tokeniser that drifts between the
    two sides degrades silently, with the sparse arm quietly returning nothing.

    The IDF half of BM25 is **not** computed here: the collection declares
    `Modifier.IDF` and Qdrant computes it server-side against the live corpus, which
    is the only place that knows the real document frequencies.
    """

    def __init__(self) -> None:
        from fastembed import SparseTextEmbedding

        self._model = SparseTextEmbedding(model_name=_BM25_MODEL)
        log.info("sparse encoder loaded", extra={"model": _BM25_MODEL})

    @staticmethod
    def _to_pairs(embedding: Any) -> tuple[list[int], list[float]]:
        return [int(i) for i in embedding.indices], [float(v) for v in embedding.values]

    def _encode(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        return [self._to_pairs(e) for e in self._model.embed(texts)]

    async def encode(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    def _encode_query(self, text: str) -> tuple[list[int], list[float]]:
        # A query is embedded with `query_embed`, not `embed`: BM25 weights a query's
        # terms differently from a document's, and using the document path for both
        # is a subtle, silent ranking regression.
        return self._to_pairs(next(iter(self._model.query_embed(text))))

    async def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        return await asyncio.to_thread(self._encode_query, text)


class Reranker:
    """A cross-encoder that scores `(question, passage)` pairs jointly.

    This is the step the 2026-10-12 spike identified as holding the remaining quality
    headroom: MiniLM retrieval already reached recall@8 = 1.00 while recall@1 was
    0.61, meaning the answering page was *being retrieved and ranked badly*. A
    bi-encoder scores the question and the passage apart and compares the results; a
    cross-encoder reads them together, which is what lets it tell "shall not exceed"
    from the twelve other clauses that mention a cap.
    """

    def __init__(self, model_name: str, *, device: str | None = None) -> None:
        from sentence_transformers import CrossEncoder

        started = time.perf_counter()
        self.model_name = model_name
        self._model = CrossEncoder(model_name, device=device)
        log.info(
            "reranker loaded",
            extra={
                "model": model_name,
                "load_ms": int((time.perf_counter() - started) * 1000),
            },
        )

    def _score(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [float(s) for s in self._model.predict(pairs, show_progress_bar=False)]

    async def score(self, question: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return await asyncio.to_thread(self._score, [(question, t) for t in texts])


class Encoders:
    """The three models as one unit, warmed together.

    Warming is explicit and happens at construction: `warm()` runs one encode of each
    so the lazy weight-loading inside torch happens where it can be timed and logged,
    not inside the first user's question.
    """

    def __init__(
        self,
        *,
        embed_model: str,
        rerank_model: str,
        device: str | None = None,
        with_reranker: bool = True,
        query_prompt: str | None = None,
    ) -> None:
        self.dense = DenseEncoder(embed_model, device=device, query_prompt=query_prompt)
        self.sparse = SparseEncoder()
        # Optional so retrieval can be measured with the reranker off — the arm the
        # eval compares against.
        self.reranker = Reranker(rerank_model, device=device) if with_reranker else None

    async def warm(self) -> None:
        probe = "The Supplier shall indemnify the Customer against all claims."
        await self.dense.encode_query(probe)
        await self.sparse.encode_query(probe)
        if self.reranker is not None:
            await self.reranker.score(probe, [probe])
        log.info("encoders warm")
