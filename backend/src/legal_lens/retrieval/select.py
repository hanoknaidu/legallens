"""Deciding which pages the model sees, and by which strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ..ai.types import SourcePage
from ..config import Settings
from ..lib_text import score_text, search_terms
from .embeddings import Embedder
from .store import VectorStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Selection:
    pages: list[SourcePage]
    path: Literal["whole_document", "rag"]
    #: Tokens the selected pages are estimated to occupy, for the audit row.
    token_estimate: int
    considered_documents: int


CHARS_PER_TOKEN = 4


def select_pages(
    *,
    question: str,
    documents: list[tuple[str, str, int]],
    pages_by_document: dict[str, list[tuple[int, str]]],
    settings: Settings,
    embedder: Embedder,
    store: VectorStore,
    owner_id: str,
) -> Selection:
    """Choose whole-corpus or RAG, and return the pages in a stable order.

    `documents` is `(document_id, title, token_estimate)`; `pages_by_document` maps a
    document id to its `(page, text)` rows.
    """
    total_tokens = sum(estimate for _, _, estimate in documents)

    if total_tokens <= settings.whole_document_token_budget:
        # Everything fits. Page order is preserved per document and documents keep
        # the order they were given, so citations map back deterministically.
        pages = [
            SourcePage(document_id=document_id, document_title=title, page=page, text=text)
            for document_id, title, _ in documents
            for page, text in pages_by_document.get(document_id, ())
        ]
        log.info(
            "retrieval: whole document",
            extra={"documents": len(documents), "pages": len(pages), "tokens": total_tokens},
        )
        return Selection(
            pages=pages,
            path="whole_document",
            token_estimate=total_tokens,
            considered_documents=len(documents),
        )

    # Over budget: retrieve. Hybrid, because the two arms fail differently — lexical
    # matching misses paraphrase, and a hashing embedder has no idea what a paraphrase
    # is, so neither is sufficient alone.
    terms = search_terms(question)
    document_ids = {document_id for document_id, _, _ in documents}
    titles = {document_id: title for document_id, title, _ in documents}

    vector_hits = store.search(
        embedder.embed(question),
        owner_id=owner_id,
        document_ids=document_ids,
        limit=settings.retrieval_candidates,
    )

    # Reciprocal-rank fusion over the two arms. Ranks rather than raw scores because
    # a cosine against a hashed vector and a lexical score are not on one scale.
    ranked: dict[tuple[str, int, int], float] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        key = (hit.chunk.document_id, hit.chunk.page_start, hit.chunk.page_end)
        ranked[key] = ranked.get(key, 0.0) + 1.0 / (60 + rank)

    lexical: list[tuple[float, tuple[str, int, int]]] = []
    for document_id in document_ids:
        for page, text in pages_by_document.get(document_id, ()):
            score = score_text(text, terms)
            if score > 0:
                lexical.append((score, (document_id, page, page)))
    lexical.sort(key=lambda row: (-row[0], row[1]))
    for rank, (_, key) in enumerate(lexical[: settings.retrieval_candidates], start=1):
        ranked[key] = ranked.get(key, 0.0) + 1.0 / (60 + rank)

    # Expand the surviving ranges to the pages they cover, de-duplicated. A chunk
    # spanning pages 4-5 contributes both, because the cited span could be on either.
    chosen: set[tuple[str, int]] = set()
    for key, score in sorted(ranked.items(), key=lambda row: (-row[1], row[0])):
        if score < settings.rerank_min_score and chosen:
            continue
        document_id, page_start, page_end = key
        for page in range(page_start, page_end + 1):
            chosen.add((document_id, page))
        if len({d for d, _ in chosen}) and len(chosen) >= settings.retrieval_top_n:
            break

    text_by_key = {
        (document_id, page): text
        for document_id in document_ids
        for page, text in pages_by_document.get(document_id, ())
    }
    pages = [
        SourcePage(
            document_id=document_id,
            document_title=titles.get(document_id, document_id),
            page=page,
            text=text_by_key[(document_id, page)],
        )
        for document_id, page in sorted(chosen)
        if (document_id, page) in text_by_key
    ]

    selected_tokens = sum(len(p.text) for p in pages) // CHARS_PER_TOKEN
    log.info(
        "retrieval: rag",
        extra={
            "documents": len(documents),
            "corpus_tokens": total_tokens,
            "pages": len(pages),
            "tokens": selected_tokens,
            "vector_hits": len(vector_hits),
            "lexical_hits": len(lexical),
        },
    )
    return Selection(
        pages=pages,
        path="rag",
        token_estimate=selected_tokens,
        considered_documents=len(documents),
    )
