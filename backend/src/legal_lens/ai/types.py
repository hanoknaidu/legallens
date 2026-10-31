"""What a generation provider is asked for, and what it returns.

The shapes here are the NFR-11 seam: `GenerationProvider` is the only thing the chat
route knows about, so swapping `echo` for Anthropic — or either for the orchestration
layer — touches no router and no API contract.

## Who retrieves

Two kinds of provider live behind this seam and they divide the work differently:

- `echo` and `anthropic` are **given** their pages. The runner selects them and hands
  them over in `GenerationRequest.pages`.
- the orchestrator **retrieves its own**, because what it sends the model and what it
  is allowed to conclude are the same decision: `Retrieved.coverage` — whether the
  selected pages are every page in scope — is what licenses "the contract is silent
  on arbitration" or forbids it. A provider handed a page list cannot know that.

`owns_retrieval` tells the runner which it is dealing with. A provider that owns
retrieval receives `scoped_document_ids` and `history` instead of `pages`, and reports
back what it actually read in `Finished.retrieval` so the audit row is about the real
request rather than about a selection that was thrown away.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..schemas import Citation, DraftPayload, Intent, SummaryField

RetrievalPath = Literal["whole_document", "rag"]


@dataclass(frozen=True)
class SourcePage:
    """One page of one document, as it will be shown to the model."""

    document_id: str
    document_title: str
    page: int
    text: str


@dataclass(frozen=True)
class HistoryTurn:
    """One prior message, for a provider that resolves references against it.

    Only the rewrite step reads this. It is on the request rather than fetched by the
    provider because the runner already holds an owner-scoped repository and the
    provider holds none — a provider that loaded its own history would be the one
    place in the service that queries messages without an owner.
    """

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class GenerationRequest:
    question: str
    intent: Intent
    #: Ordered, and the order is load-bearing: a citation comes back as an index into
    #: this list, so the mapping from model output to real pages depends on it. Empty
    #: for a provider that owns retrieval — see the module docstring.
    pages: list[SourcePage]
    retrieval_path: RetrievalPath
    #: The documents the user scoped the question to, or empty for "everything ready".
    #: Only read by a provider that retrieves for itself.
    scoped_document_ids: list[str] = field(default_factory=list)
    #: Whose request this is. On the request rather than left implicit because a
    #: provider that owns its retrieval filters its index on it — defence in depth
    #: behind the document scope, which is resolved from the same owner. A provider
    #: handed pages has no use for it.
    owner_id: str = ""
    #: Oldest first, excluding the turn being generated.
    history: list[HistoryTurn] = field(default_factory=list)


@dataclass
class Usage:
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: Which step spent it — `generation:ask`, `rewrite`, `summary:retry`. Recorded
    #: per call rather than per turn so "what is the rewrite step costing us?" is
    #: answerable from the cost table later, without new instrumentation.
    purpose: str = ""
    latency_ms: int = 0


@dataclass(frozen=True)
class RewriteRecord:
    """What the pre-retrieval rewrite step concluded.

    Recorded on the audit row rather than only logged, because the rewrite is the one
    step whose failures are invisible in the answer: retrieval fetches the wrong
    passages, the prose comes back fluent and confident, and the attorney has no way
    to see that the question they asked was replaced with a different one. "Was
    retrieval even looking for the right thing?" is the first question asked of a
    wrong answer, and stdout is not where the answer to it should live.
    """

    #: What the attorney typed.
    original: str
    #: What retrieval actually searched for. Equal to `original` when nothing changed.
    searched_for: str
    rewritten: bool
    #: `empty_history`, `model`, `declined`, `unusable`, `error` — see `RewriteResult`.
    source: str
    #: The model's one-line account of what it resolved, or why it declined.
    reason: str = ""


@dataclass(frozen=True)
class RetrievalReport:
    """What a self-retrieving provider actually read.

    The audit row is a record of what the model was shown (FR-20). When the provider
    chose the pages, only the provider can say — so it says, rather than the runner
    reporting a selection it did not use.
    """

    path: RetrievalPath
    document_ids: list[str]
    pages: int
    #: True when the pages sent were every page in scope. The flag that decides
    #: whether an absence was provable, which is worth keeping next to the answer it
    #: licensed.
    coverage: bool
    #: The query rewrite, when the provider ran one. `None` for a provider with no
    #: pre-retrieval step, which is different from a rewrite that declined.
    rewrite: RewriteRecord | None = None


# ---------------------------------------------------------------------------
# Stream events. The provider yields these; the SSE route translates them to the
# wire protocol the frontend already reads.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class CitationFound:
    """A grounded span. `n` is assigned by the provider, starting at 1."""

    citation: Citation


@dataclass(frozen=True)
class Structured:
    """A summary or draft payload, which resolves as a unit rather than streaming."""

    summary: list[SummaryField] | None = None
    draft: DraftPayload | None = None


@dataclass(frozen=True)
class Abstained:
    """The documents do not support an answer. A correct outcome (FR-9)."""

    message: str


@dataclass
class Finished:
    #: The headline call, kept for providers that make exactly one.
    usage: Usage = field(default_factory=Usage)
    #: Every model call the turn made, including the headline one, when there was
    #: more than one. A workflow that rewrites the query before answering spends on
    #: two models at two tiers; billing them as one row loses which was which.
    #: Empty means "`usage` is the whole story".
    calls: list[Usage] = field(default_factory=list)
    #: Set only by a provider that did its own retrieval.
    retrieval: RetrievalReport | None = None


GenerationEvent = TextDelta | CitationFound | Structured | Abstained | Finished


class GenerationProvider(Protocol):
    """Streams an answer grounded in the supplied pages.

    Implementations must not invent a citation: every `CitationFound` has to quote
    text that appears verbatim in one of the pages that were sent, because the viewer
    highlights by finding that span in the stored page text. A paraphrased citation
    opens the right page and highlights nothing — the silent failure NFR-1 forbids.
    """

    name: str

    #: True when this provider selects its own pages and `request.pages` is empty.
    #: Read by the runner, which skips selection entirely for such a provider rather
    #: than computing a page list for it to ignore.
    owns_retrieval: bool

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]: ...
