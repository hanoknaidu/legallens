"""Reordering fused candidates, and expanding what survives to whole pages.

Kept free of the store and the models so it can be tested as arithmetic: both
functions take values and return values.
"""

from __future__ import annotations

import logging

from ..types import Passage
from .encode import Reranker

log = logging.getLogger(__name__)


async def rerank(
    reranker: Reranker | None,
    question: str,
    candidates: list[Passage],
    *,
    top_n: int,
    min_score: float | None = None,
) -> list[tuple[Passage, float]]:
    """Score `(question, passage)` pairs jointly and keep the best `top_n`.

    With no reranker the fused order is kept as-is, which is the arm the eval compares
    against — the point of measuring is to show the cross-encoder earns its latency.

    `min_score` is **off by default**, because a floor is only meaningful on a known
    score scale and cross-encoders do not share one — `ms-marco-MiniLM-L-6-v2`
    returns raw logits that sit around -10 even for the passage that answers the
    question. A floor of 0.0 against that model drops every passage, which presents
    as an oddly incurious assistant rather than as a retrieval failure.

    When a floor is set it is applied **after** the sort and never to the last
    passage standing: a question whose every candidate scores poorly should still
    produce the best of a bad set and let the generation step abstain on the
    evidence, rather than reach the model with no documents and abstain because
    nothing was retrieved. Those are different findings and the attorney should not
    see them worded the same way.
    """
    if not candidates:
        return []

    if reranker is None:
        return [(p, 0.0) for p in candidates[:top_n]]

    scores = await reranker.score(question, [p.text for p in candidates])
    ranked = sorted(
        zip(candidates, scores, strict=True),
        # Ties broken by document and page so an identical request ranks identically.
        # Citation mapping depends on a stable order.
        key=lambda row: (-row[1], row[0].doc_id, row[0].page_start),
    )

    kept = list(ranked[:top_n])
    if min_score is not None:
        kept = [row for row in kept if row[1] >= min_score] or ranked[:1]

    log.info(
        "reranked",
        extra={
            "candidates": len(candidates),
            "kept": len(kept),
            "top_score": round(kept[0][1], 4),
            "cut_score": round(kept[-1][1], 4),
        },
    )
    return kept


def covered_pages(passages: list[Passage]) -> list[tuple[str, int]]:
    """Every `(doc_id, page)` the passages touch, de-duplicated and ordered.

    A passage spanning pages 4-5 contributes both. The cited span could be on either
    one, and sending only the page the chunk started on is how a citation ends up
    pointing at a page whose text does not contain it.
    """
    seen: set[tuple[str, int]] = set()
    for passage in passages:
        for page in passage.pages:
            seen.add((passage.doc_id, page))
    return sorted(seen)
