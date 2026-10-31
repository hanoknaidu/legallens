"""The ask workflow: a grounded answer to one question.

```
question + history
   │
   ├─ 0. history empty?  ──yes──► skip the rewrite entirely (code, not prompt)
   │
   ├─ 1. PRE-RETRIEVAL   Haiku 4.5: rewrite the question, or decline to
   │
   ├─ 2. RETRIEVAL       small corpus → every page; otherwise Qdrant hybrid:
   │                     dense top-25 ∥ BM25 top-25 → server-side RRF
   │
   ├─ 3. RERANK          cross-encoder over the fused candidates → top 8
   │                     (skipped on the whole-document route)
   │
   ├─ 4. AUGMENT         pages → custom-content document blocks, citations enabled
   │
   └─ 5. GENERATE        Sonnet 5.5, streamed, native citations
```

## The absence problem, and how this handles it

*"Is there an arbitration clause?"* cannot be answered from eight pages of two
hundred, because the clause could be on any of the other hundred and ninety-two.
Answering "the contract is silent on arbitration" there is a confident guess dressed
as a finding, and FR-13 turns on not making it.

Two mechanisms, and they work together:

1. **The route.** Below `whole_document_max_tokens` the model is sent every page in
   scope, so an absence is a fact it can observe rather than infer. See
   `Retriever.select` — a 28-page MSA takes this route, which is the common case.
2. **The prompt.** Above that budget retrieval selects, and the prompt is conditioned
   on `Retrieved.coverage` so the unsafe claim is unavailable:

- **Full coverage** (a small corpus, where retrieval returned everything): the model
  may state that the documents are silent, because it read all of them.
- **Partial coverage**: it may not. It is told explicitly that it is seeing a
  selection, and that the strongest claim available to it is that *the excerpts
  provided* do not address the question. The abstention message the user sees is
  worded the same way.

No classifier, no size threshold and no extra model call — the unsafe claim is simply
not available to the model in the case where it would be unsafe.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import OrchestratorConfig
from ..retrieve import Retriever
from ..types import Citation, Retrieved, Turn, Usage
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

PROMPT_FILENAME = "ask_prompt.md"

#: Used when the model saw every page in scope. An absence is provable here.
FULL_COVERAGE_NOTICE = """You have been given **every page of every document in \
scope**. Nothing has been withheld or summarised.

Because you have read all of it, you may state that the documents do not contain \
something — if a clause is not in what you were given, it is not in the documents. \
Reporting that absence is useful and correct."""

#: Used when retrieval selected a subset. An absence is *not* provable here, and the
#: difference is the whole point of this notice.
PARTIAL_COVERAGE_NOTICE = """You have been given **a selection of excerpts** that a \
search retrieved as most relevant to this question. You have **not** been shown the \
whole of these documents, and relevant language may exist on pages you cannot see.

This constrains what you are allowed to conclude:

- **Never state that the documents are silent on something, that they do not contain \
a clause, or that no such provision exists.** You cannot know that from a selection, \
and asserting it is the most damaging mistake available to you here.
- If the excerpts do not answer the question, reply with exactly `NOT_IN_DOCUMENTS`. \
In this context that means "not in the excerpts provided" — it is not a finding about \
the documents as a whole, and it will be reported to the attorney that way."""

#: Wording matched to each case, because the sentinel means different things.
FULL_COVERAGE_ABSTENTION = (
    "The documents in scope do not address this question. No answer was inferred from outside them."
)
PARTIAL_COVERAGE_ABSTENTION = (
    "The excerpts retrieved for this question do not address it. This is not a "
    "finding that the documents are silent — only the retrieved pages were read."
)


def load_prompt() -> str:
    return (Path(__file__).parents[1] / PROMPT_FILENAME).read_text(encoding="utf-8")


def system_prompt(*, coverage: bool, template: str | None = None) -> str:
    """The ask system prompt, with the coverage notice filled in."""
    return (template if template is not None else load_prompt()).replace(
        "{coverage_notice}",
        FULL_COVERAGE_NOTICE if coverage else PARTIAL_COVERAGE_NOTICE,
    )


@dataclass
class AskResult:
    """Everything the turn produced, for persistence and for the audit row."""

    question: str
    #: What retrieval actually searched for — the rewrite when there was one.
    searched_for: str
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    abstained: bool = False
    #: A factual answer with no citation is ungrounded by definition (NFR-1).
    grounded: bool = True
    coverage: bool = False
    pages: int = 0
    candidates: int = 0
    #: `whole_document` or `rag` — which route chose those pages.
    path: str = "rag"
    #: The estimated size of the corpus the route was chosen from.
    corpus_tokens: int = 0
    rewrite: RewriteResult | None = None
    #: One row per model call, so the rewrite's cost is separable from the answer's.
    usage: list[Usage] = field(default_factory=list)


class AskWorkflow:
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
        self._prompt = prompt
        self._client = client or build_client(config.require_api_key(), config.headers)

    def _rewrite(self, question: str, history: list[Turn]) -> RewriteResult:
        if self._rewriter is None:
            self._rewriter = QueryRewriter(self._config)
        return self._rewriter.rewrite(question, history)

    async def run(
        self,
        question: str,
        *,
        history: list[Turn] | None = None,
        doc_ids: set[str] | None = None,
    ) -> AsyncIterator[GenerationEvent | AskResult]:
        """Run the pipeline, streaming the answer.

        Yields generation events as they arrive and exactly one `AskResult` last, so a
        caller can render tokens live and still persist the whole turn afterwards.
        """
        turns = history or []
        result = AskResult(question=question, searched_for=question)

        # Steps 0 and 1. With no history this returns without a model call.
        rewrite = self._rewrite(question, turns)
        result.rewrite = rewrite
        result.searched_for = rewrite.question
        if rewrite.usage is not None:
            result.usage.append(rewrite.usage)

        # Steps 2 and 3. `select` reads the whole corpus when it fits the
        # whole-document budget and retrieves when it does not — the FR-13 decision,
        # see `Retriever.select`.
        retrieved: Retrieved = await self._retriever.select(
            rewrite.question, doc_ids=doc_ids, top_n=self._config.ask_top_n
        )
        result.coverage = retrieved.coverage
        result.pages = len(retrieved.pages)
        result.candidates = retrieved.candidates
        result.path = retrieved.path
        result.corpus_tokens = retrieved.corpus_tokens

        # Steps 4 and 5.
        async for event in stream_grounded(
            self._client,
            model=self._config.ask_model,
            system=system_prompt(coverage=retrieved.coverage, template=self._prompt),
            pages=retrieved.pages,
            question=rewrite.question,
            effort=self._config.ask_effort,
            max_tokens=self._config.max_output_tokens,
            purpose="ask",
            abstention_message=(
                FULL_COVERAGE_ABSTENTION if retrieved.coverage else PARTIAL_COVERAGE_ABSTENTION
            ),
            # Kept on for `ask`, unlike summary and draft: a conversation asks several
            # questions of the same corpus, and on the whole-document route the prefix
            # is byte-identical between turns. Measured: one 19,955-token write served
            # 39,910 tokens of reads across three turns.
            cache_documents=True,
            # The answer is prose, so each cited claim carries its chip inline. Rule 5
            # of the prompt forbids the model from writing citations itself; these are
            # injected from the API's own block-to-page mapping.
            inline_markers=True,
        ):
            if isinstance(event, Completed):
                result.answer = event.text or result.answer
                result.citations = event.citations
                result.abstained = event.abstained
                result.usage.append(event.usage)
            elif isinstance(event, Abstained):
                result.abstained = True
                result.answer = event.message
                yield event
            elif isinstance(event, TextDelta | CitationFound):
                yield event

        # An abstention is grounded on its own terms: it is a claim about the
        # documents, not an assertion that needs one.
        result.grounded = result.abstained or bool(result.citations)
        if not result.grounded:
            log.warning(
                "ask produced an uncited answer",
                extra={"chars": len(result.answer), "pages": result.pages},
            )

        yield result
