"""The draft workflow: new prose the attorney will edit and send.

Opus 5.5, because this is the output with the highest cost of being subtly wrong —
the attorney may put their name on it.

Two things differ from `ask`, and both follow from what a memo is.

**A wider keep: 12 passages rather than 8.** A memo cites more ground than an answer
does, and a clause that never reached the prompt does not fail loudly — it becomes an
`[ATTORNEY INPUT REQUIRED]` the attorney then spends time researching by hand, for a
fact that was in the corpus all along.

**The marker is the deliverable.** FR-16 and FR-17 turn on `[ATTORNEY INPUT REQUIRED]`
appearing wherever the documents do not supply a fact the draft needs. A model's
instinct is to produce smooth prose, and smooth prose over a gap is the single most
dangerous output this product can make: it is indistinguishable from drafted text, so
the attorney has no signal to check it. `draft_prompt.md` is therefore written to make
marking *cheaper* than writing around a gap, and this module counts the markers so
that a draft which produced none over a corpus that cannot support it is visible
rather than assumed fine.

Model constraints: thinking cannot be disabled on Opus 5.5 at any effort,
`budget_tokens` is a 400, and the default effort is `medium` — so `high` is set
explicitly by config rather than left to a default that differs between models.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import OrchestratorConfig
from ..retrieve import Retriever
from ..types import Citation, Turn, Usage
from .generate import (
    Abstained,
    CitationFound,
    Completed,
    GenerationEvent,
    TextDelta,
    build_client,
    stream_grounded,
)
from .rewrite import QueryRewriter, RewriteResult

log = logging.getLogger(__name__)

PROMPT_FILENAME = "draft_prompt.md"

#: FR-16/FR-17. Exact — it is styled on render and pinned by the export tests.
MARKER = "[ATTORNEY INPUT REQUIRED]"

#: Appended when the draft was written from a selection rather than the whole corpus,
#: so the model does not assert an absence it cannot prove. Same reasoning as the ask
#: workflow's coverage notice.
PARTIAL_COVERAGE_NOTICE = """

## What you have been given

You have been given **a selection of excerpts** retrieved as most relevant to this \
request, not the whole of these documents. Never write that the documents do not \
contain something, and never describe a provision as absent — you cannot know that \
from a selection. Where the draft needs a fact you cannot find, that is exactly what \
`[ATTORNEY INPUT REQUIRED]` is for."""


def load_prompt() -> str:
    return (Path(__file__).parents[1] / PROMPT_FILENAME).read_text(encoding="utf-8")


@dataclass
class DraftResult:
    request: str
    searched_for: str
    title: str = ""
    body: str = ""
    citations: list[Citation] = field(default_factory=list)
    #: How many gaps the draft declared. Zero on a corpus that cannot support the
    #: request is a warning sign, not a clean result.
    markers: int = 0
    abstained: bool = False
    coverage: bool = False
    pages: int = 0
    #: `whole_document` or `rag` — which route chose those pages.
    path: str = "rag"
    corpus_tokens: int = 0
    rewrite: RewriteResult | None = None
    usage: list[Usage] = field(default_factory=list)


#: The imperative a drafting request opens with, plus the filler after it.
#:
#: Stripped because a request and a title are different things: *"Draft a short memo
#: on the termination provisions"* is the instruction, and *"Memo on the termination
#: provisions"* is what the attorney is now reading. The old behaviour put the
#: imperative on the rendered memo, which read as though the document were still a
#: to-do item.
#:
#: The rule is deliberately narrow — **drop the verb and its filler, keep the noun
#: phrase**. An earlier attempt also swallowed the document noun and the preposition
#: after it, which turned *"Prepare an email to the counterparty"* into *"Counterparty"*
#: and lost what the document was. One rule, applied to the front of the string, is
#: predictable in a way that a chain of special cases is not.
#:
#: Alternations are longest-first throughout: Python's alternation is leftmost-wins,
#: so `memo|memorandum` matches "memo" inside "memorandum" and leaves "randum", and
#: `a|an` matches "a" inside "an" and leaves "n".
_LEADING_INSTRUCTION = re.compile(
    r"""^\s*
    (?:please\s+)?
    (?:draft|write|prepare|produce|create|generate|compose|put\s+together|give\s+me)
    \s+
    (?:me\s+)?
    (?:(?:an|a|the)\s+)?
    (?:(?:short|brief|quick|detailed|comprehensive|one-page|two-page)\s+)*
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Below this, stripping has eaten the whole request and the original reads better.
#: "Draft a memo" strips to "memo", which is not a title; the request is.
_MIN_TITLE_CHARS = 4


def title_for(request: str) -> str:
    """A document title from the drafting request.

    Deliberately mechanical — no model call. Naming a document the attorney is about
    to retitle is not worth a round trip, and a mechanical title is predictable in a
    way a generated one is not.
    """
    cleaned = request.strip().rstrip("?.").strip()
    if not cleaned:
        return "Draft"

    stripped = _LEADING_INSTRUCTION.sub("", cleaned).strip()
    if len(stripped) >= _MIN_TITLE_CHARS:
        cleaned = stripped

    # Not `.title()`: that mangles "MSA" into "Msa" and "non-compete" into
    # "Non-Compete", and the document's own capitalisation is usually right.
    return (cleaned[:1].upper() + cleaned[1:])[:120]


class DraftWorkflow:
    def __init__(
        self,
        config: OrchestratorConfig,
        retriever: Retriever,
        *,
        rewriter: QueryRewriter | None = None,
        client: Any = None,
        prompt: str | None = None,
    ) -> None:
        self._config = config
        self._retriever = retriever
        self._rewriter = rewriter
        self._prompt = prompt if prompt is not None else load_prompt()
        self._client = client or build_client(config.require_api_key(), config.headers)

    def _rewrite(self, request: str, history: list[Turn]) -> RewriteResult:
        if self._rewriter is None:
            self._rewriter = QueryRewriter(self._config)
        return self._rewriter.rewrite(request, history)

    async def run(
        self,
        request: str,
        *,
        history: list[Turn] | None = None,
        doc_ids: set[str] | None = None,
    ) -> AsyncIterator[GenerationEvent | DraftResult]:
        turns = history or []
        result = DraftResult(request=request, searched_for=request, title=title_for(request))

        rewrite = self._rewrite(request, turns)
        result.rewrite = rewrite
        result.searched_for = rewrite.question
        if rewrite.usage is not None:
            result.usage.append(rewrite.usage)

        # Same route decision as `ask`, and it matters more here: a gap the draft
        # marks `[ATTORNEY INPUT REQUIRED]` because the clause was never retrieved is
        # work the attorney does by hand for a fact that was in the corpus.
        retrieved = await self._retriever.select(
            rewrite.question, doc_ids=doc_ids, top_n=self._config.draft_top_n
        )
        result.coverage = retrieved.coverage
        result.pages = len(retrieved.pages)
        result.path = retrieved.path
        result.corpus_tokens = retrieved.corpus_tokens

        system = self._prompt if retrieved.coverage else self._prompt + PARTIAL_COVERAGE_NOTICE

        async for event in stream_grounded(
            self._client,
            model=self._config.draft_model,
            system=system,
            pages=retrieved.pages,
            question=rewrite.question,
            effort=self._config.draft_effort,
            # A memo truncated mid-clause looks finished and is not, which is why
            # this budget is twice the answer budget.
            max_tokens=self._config.draft_max_output_tokens,
            purpose="draft",
            # A draft is one call with no retry pass, so a cache write here has no
            # possible reader — it was a flat 25% surcharge on the largest part of
            # the most expensive request in the product. See `citations.cache_last`.
            cache_documents=False,
            # A memo asserts things about the agreement, and a reviewing attorney has
            # to be able to get from an assertion to the clause behind it. The body is
            # rendered through the same markdown renderer as an answer, so the same
            # markers produce the same chips.
            inline_markers=True,
        ):
            if isinstance(event, Completed):
                result.body = event.text.strip()
                result.citations = event.citations
                result.abstained = event.abstained
                result.usage.append(event.usage)
            elif isinstance(event, Abstained):
                result.abstained = True
                result.body = event.message
                yield event
            elif isinstance(event, TextDelta | CitationFound):
                yield event

        result.markers = result.body.count(MARKER)
        log.info(
            "drafted",
            extra={
                "chars": len(result.body),
                "citations": len(result.citations),
                "markers": result.markers,
                "pages": result.pages,
                "coverage": result.coverage,
            },
        )
        if not result.abstained and result.markers == 0 and not retrieved.coverage:
            # Not an error — a complete draft from a partial corpus is possible. But
            # it is the shape a confidently-invented draft also has, so it is worth a
            # line in the log rather than silence.
            log.info("draft declared no gaps from a partial corpus; worth a look")

        yield result
