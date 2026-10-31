"""The retrieval pipeline the three workflows share.

```
question ─► corpus fits the whole-document budget? ──yes──► every page in scope
                             │ no
                             ▼
            Qdrant: dense top-k ∥ BM25 top-k ─► server-side RRF
                                                      │
                            cross-encoder rerank ◄─────┘
                                     │
                       expand survivors to whole pages
                                     │
                         pages, in document order
```

`select()` makes that first decision and is what the workflows call; `search()` and
`whole()` are the two arms, callable directly by `summary` and by the eval harness.

**The prompt is always built from pages, never from chunks.** A chunk that straddles
a page boundary has no single page number, so a citation into it could not be resolved
to one — and the whole citation mechanism depends on a content block being exactly one
page. Retrieval therefore ranks chunks and then expands what survives into the pages
those chunks cover, which is why this class needs a page source as well as an index.
"""

from __future__ import annotations

import logging

from ..config import OrchestratorConfig
from ..types import Page, Passage, Retrieved
from .encode import Encoders
from .index import PassageIndex
from .rerank import covered_pages, rerank

log = logging.getLogger(__name__)


class PageStore:
    """Every page of every document, by `(doc_id, page)`.

    Fed from the eval corpus by the CLI, and from `documents.all_pages()` at
    integration time. Held outside Qdrant deliberately: page text is not a vector
    concern, and duplicating it into the index would make a re-embed a re-write of the
    corpus.
    """

    def __init__(self) -> None:
        self._text: dict[tuple[str, int], str] = {}
        self._title: dict[str, str] = {}
        self._pages: dict[str, list[int]] = {}
        self._owner: dict[str, str] = {}

    def add_document(
        self, doc_id: str, title: str, pages: list[tuple[int, str]], owner_id: str = ""
    ) -> None:
        self._title[doc_id] = title
        self._pages[doc_id] = sorted(page for page, _ in pages)
        self._owner[doc_id] = owner_id
        for page, text in pages:
            self._text[(doc_id, page)] = text

    def owner_of(self, doc_id: str) -> str:
        return self._owner.get(doc_id, "")

    def scoped_to(self, doc_ids: set[str] | None, owner_id: str | None) -> set[str] | None:
        """`doc_ids` narrowed to the documents this owner actually has.

        The page store holds every owner's pages, exactly as the index does, so the
        same defence-in-depth argument applies: scoping is the caller's job and this
        makes the caller unable to get it wrong. A document whose owner is unknown —
        the eval corpus, whose fixtures carry none — is left in scope, because the
        single-tenant case has no owner to disagree with.
        """
        if not owner_id:
            return doc_ids
        wanted = doc_ids if doc_ids is not None else self.document_ids()
        return {d for d in wanted if self._owner.get(d, owner_id) == owner_id}

    def page(self, doc_id: str, page: int) -> Page | None:
        text = self._text.get((doc_id, page))
        if text is None:
            return None
        return Page(doc_id=doc_id, title=self._title.get(doc_id, doc_id), page=page, text=text)

    def document_ids(self) -> set[str]:
        return set(self._pages)

    def title(self, doc_id: str) -> str:
        return self._title.get(doc_id, doc_id)

    def count_pages(self, doc_ids: set[str] | None = None) -> int:
        wanted = doc_ids or self.document_ids()
        return sum(len(self._pages.get(d, ())) for d in wanted)

    def count_tokens(self, doc_ids: set[str] | None = None) -> int:
        """Roughly how many tokens the pages in scope would cost to send.

        Four characters per token, which is the same estimate `select_pages` in the
        service uses — the two decide the same question and disagreeing about the
        size of a corpus would be worse than either being imprecise. Deliberately an
        estimate: the alternative is a tokeniser call per page on every question, to
        choose between two routes whose boundary is not sharp.
        """
        wanted = sorted(doc_ids or self.document_ids())
        return sum(
            len(self._text.get((doc_id, page), "")) // 4
            for doc_id in wanted
            for page in self._pages.get(doc_id, ())
        )

    def all_pages(self, doc_ids: set[str] | None = None) -> list[Page]:
        """Every page in scope, in document then page order."""
        wanted = sorted(doc_ids or self.document_ids())
        out: list[Page] = []
        for doc_id in wanted:
            for page in self._pages.get(doc_id, ()):
                resolved = self.page(doc_id, page)
                if resolved is not None:
                    out.append(resolved)
        return out


class Retriever:
    """Hybrid search, reranking and page expansion, in one call."""

    def __init__(
        self,
        config: OrchestratorConfig,
        index: PassageIndex,
        pages: PageStore,
        encoders: Encoders,
    ) -> None:
        self._config = config
        self._index = index
        self._pages = pages
        self._encoders = encoders

    async def select(
        self,
        question: str,
        *,
        doc_ids: set[str] | None = None,
        owner_id: str | None = None,
        top_n: int | None = None,
    ) -> Retrieved:
        """Read every page in scope, or retrieve — whichever the corpus size calls for.

        **This is the FR-13 decision, not an optimisation.** Hybrid retrieval keeps a
        fixed number of passages, so on any corpus bigger than that selection
        `coverage` is false, and under partial coverage the prompt forbids the model
        from reporting an absence. That is the right constraint and the wrong default:
        it means *"is there a non-compete clause?"* abstains on a 28-page contract
        that fits in one prompt eight times over, and FR-13 is exactly that question.

        So below `whole_document_max_tokens` the whole corpus goes to the model and an
        absence becomes provable; above it, retrieval earns its place and the partial
        coverage notice applies. The threshold is BRD §4.2's, and `workflows-plan.md`
        §1 recommended it over always-retrieving.

        `search()` and `whole()` remain callable directly: `summary` always wants the
        whole corpus whatever its size, and the eval harness pins the retrieval arm.
        """
        # Narrowed once, here, so the size estimate the route is chosen from is the
        # size of what this owner can actually be sent.
        doc_ids = self._pages.scoped_to(doc_ids, owner_id)
        budget = self._config.whole_document_max_tokens
        corpus_tokens = self._pages.count_tokens(doc_ids)

        if budget > 0 and corpus_tokens <= budget:
            retrieved = await self.whole(doc_ids, owner_id=owner_id)
            log.info(
                "whole-document read",
                extra={
                    "pages": len(retrieved.pages),
                    "corpus_tokens": corpus_tokens,
                    "budget": budget,
                    # True by construction here, and logged anyway: it is what
                    # licenses the model to report an absence.
                    "coverage": retrieved.coverage,
                },
            )
            return retrieved

        retrieved = await self.search(question, doc_ids=doc_ids, owner_id=owner_id, top_n=top_n)
        retrieved.corpus_tokens = corpus_tokens
        log.info(
            "corpus exceeds the whole-document budget; retrieved",
            extra={"corpus_tokens": corpus_tokens, "budget": budget},
        )
        return retrieved

    async def search(
        self,
        question: str,
        *,
        doc_ids: set[str] | None = None,
        owner_id: str | None = None,
        top_n: int | None = None,
    ) -> Retrieved:
        keep = top_n or self._config.ask_top_n
        doc_ids = self._pages.scoped_to(doc_ids, owner_id)
        corpus_pages = self._pages.count_pages(doc_ids)

        # Both filters travel with the query: the documents the attorney chose, and
        # the owner the service guarantees. See `PassageIndex._scope_filter`.
        hits = await self._index.hybrid_search(
            question, doc_ids=doc_ids, owner_id=owner_id, limit=self._config.candidates
        )
        candidates: list[Passage] = [passage for passage, _ in hits]

        kept = await rerank(
            self._encoders.reranker,
            question,
            candidates,
            top_n=keep,
            min_score=self._config.rerank_min_score,
        )

        pages = [
            page
            for doc_id, number in covered_pages([passage for passage, _ in kept])
            if (page := self._pages.page(doc_id, number)) is not None
        ]

        retrieved = Retrieved(
            pages=pages,
            candidates=len(candidates),
            kept=len(kept),
            corpus_pages=corpus_pages,
            path="rag",
            corpus_tokens=self._pages.count_tokens(doc_ids),
        )
        log.info(
            "retrieved",
            extra={
                "candidates": retrieved.candidates,
                "kept": retrieved.kept,
                "pages": len(retrieved.pages),
                "corpus_pages": corpus_pages,
                # The flag the ask prompt is conditioned on: whether an absence can
                # honestly be asserted, or only the weaker "the excerpts do not say".
                "coverage": retrieved.coverage,
            },
        )
        return retrieved

    async def whole(
        self, doc_ids: set[str] | None = None, owner_id: str | None = None
    ) -> Retrieved:
        """Every page in scope, no retrieval.

        What `summary` uses. A summary of retrieved fragments is not a summary, and
        the nine fields include *Open items* — an absence question, which fragments
        cannot answer.

        `async` with nothing to await: the owner-scoped wrapper the service hands the
        workflows has to be, since it may still be building the index, and a method
        that is sync on one implementation and async on the other is a trap for the
        caller. One shape.
        """
        doc_ids = self._pages.scoped_to(doc_ids, owner_id)
        pages = self._pages.all_pages(doc_ids)
        return Retrieved(
            pages=pages,
            candidates=len(pages),
            kept=len(pages),
            corpus_pages=self._pages.count_pages(doc_ids),
            path="whole_document",
            corpus_tokens=self._pages.count_tokens(doc_ids),
        )
