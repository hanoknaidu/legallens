"""The values the three workflows pass between their steps.

Deliberately plain dataclasses with no Pydantic and no imports from the rest of
`legal_lens`. At integration time these map onto `schemas.Citation` and friends in one
adapter; keeping them separate here is what lets the workflows run with nothing else
booted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Passage:
    """One indexed unit of one document.

    The unit is whatever the caller indexed — a page from the eval corpus, a chunk row
    from `chunk_metadata` at integration time. This package deliberately owns no
    chunker: duplicating `ingestion/chunk.py` would let the two drift, and a retrieval
    index built on different boundaries than the service's is worse than no index.

    `page_start`/`page_end` differ when a chunk straddles a page boundary, which is
    why survivors are expanded to every page they span: the cited span could be on
    either one.
    """

    doc_id: str
    title: str
    page_start: int
    page_end: int
    text: str
    #: The owner this text belongs to, carried into the index payload so the store
    #: can filter on it. Defaulted because the eval corpus is single-tenant and its
    #: fixtures predate this field; in the service it is always set. See
    #: `PassageIndex._scope_filter` for why a default is safe here and an unfiltered
    #: *search* is not.
    owner_id: str = ""

    @property
    def pages(self) -> range:
        return range(self.page_start, self.page_end + 1)


@dataclass(frozen=True)
class Page:
    """One page as the model will see it: exactly one content block."""

    doc_id: str
    title: str
    page: int
    text: str


@dataclass(frozen=True)
class Citation:
    """A grounded span.

    `cited_text` comes back verbatim from the block that was sent, which is what the
    viewer highlights with. A paraphrased citation opens the right page and highlights
    nothing — the silent failure NFR-1 forbids — so nothing in this package ever
    constructs one from model prose.
    """

    n: int
    doc_id: str
    doc_title: str
    page: int
    cited_text: str


@dataclass
class Usage:
    """What one model call cost, in tokens."""

    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: Which step spent it — `rewrite`, `ask`, `summary`, `summary:retry`, `draft`.
    #: Recorded per call rather than per question so that "what is the rewrite step
    #: costing us?" is answerable from data later, without new instrumentation.
    purpose: str = ""
    latency_ms: int = 0


@dataclass(frozen=True)
class Turn:
    """One prior message, as the rewrite step reads it."""

    role: Literal["user", "assistant"]
    content: str


#: Which route put pages in front of the model. `whole_document` means every page in
#: scope was sent, so an absence is provable; `rag` means a selection was retrieved.
#: Recorded rather than inferred from `coverage`, because the two can agree by
#: accident — hybrid retrieval on a two-page NDA returns both pages and looks like a
#: whole-document read, and the audit row should say which decision was actually made.
RetrievalPath = Literal["whole_document", "rag"]


@dataclass
class Retrieved:
    """What retrieval selected, and how much of the corpus it stands for."""

    pages: list[Page] = field(default_factory=list)
    #: Candidates the two arms produced before reranking, for the log.
    candidates: int = 0
    #: Passages that survived the reranker, before page expansion.
    kept: int = 0
    #: Every page that was in scope, whether or not it was selected.
    corpus_pages: int = 0
    #: How these pages were chosen. See `RetrievalPath`.
    path: RetrievalPath = "rag"
    #: The corpus size that the route was chosen from, in estimated tokens. Kept so
    #: that "why did this question retrieve rather than read everything?" is
    #: answerable from the audit row rather than by re-deriving the estimate.
    corpus_tokens: int = 0

    @property
    def coverage(self) -> bool:
        """True when the selected pages are *every* page in scope.

        This is the honesty flag for absence questions. "The contract is silent on
        arbitration" is only provable if the whole contract was read; from eight pages
        of two hundred it is a confident guess. On a small corpus retrieval returns
        everything and nothing is lost, so the claim is safe — and the ask prompt is
        conditioned on exactly this, rather than on a size threshold.
        """
        return self.corpus_pages > 0 and len(self.pages) >= self.corpus_pages

    @property
    def documents(self) -> int:
        return len({p.doc_id for p in self.pages})
