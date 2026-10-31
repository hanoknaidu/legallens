"""The three workflows, behind the generation seam.

`orchestrator/` routes a message to `ask`, `summary` or `draft` and runs each one end
to end over hybrid retrieval. This provider is the whole of its integration into the
request path: it translates one type universe into the other (through `adapt.py`),
dispatches on intent, and reports what retrieval actually did so the audit row
describes the real request.

## It retrieves for itself

`owns_retrieval = True`, so `TurnRunner` hands over `scoped_document_ids` and skips
selection. That is not an optimisation. `Retrieved.coverage` — whether the pages sent
were *every* page in scope — is what licenses the model to say "the contract is silent
on arbitration" or forbids it, and the prompt is built from that flag. A provider
handed a page list by someone else cannot know which case it is in, so the decision of
what to send and the decision of what may be concluded have to be made together.

## Summary and draft stream no text

Only citations, then one structured payload. `echo.py` states the reason — a
half-rendered summary table reads as broken — and the frontend agrees: a summary or
draft message carries its payload in `summary`/`draft` and `content: ""`, with
`Message.tsx` rendering the card and never the prose. For summary it also sidesteps
the retry pass, which re-emits the headings it was asked to fill and would otherwise
append a second copy of them to the transcript.

## The rewrite step runs in a thread

`QueryRewriter.rewrite()` is a synchronous LangChain call made from inside the
workflows' async `run()`. That is right for the CLI, which has no event loop to block,
and wrong here: a Haiku round trip on the loop stalls every other request in the
process, including `/health`. So the rewrite is performed in a worker thread first and
the finished result is injected — see `_PreparedRewriter`. The alternative, making
`rewrite()` async, ripples through the CLI, the rewrite suite and their tests to buy
the same behaviour.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..config import Settings
from ..schemas import DraftPayload
from .adapt import to_orch_turns, to_usage, to_wire_citation, to_wire_summary_field
from .types import (
    Abstained,
    CitationFound,
    Finished,
    GenerationEvent,
    GenerationRequest,
    RetrievalReport,
    RewriteRecord,
    Structured,
    TextDelta,
    Usage,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..orchestrator.workflows import RewriteResult
    from ..retrieval.orchestrated import OrchestratedRetrieval

log = logging.getLogger(__name__)

NO_SCOPE_MESSAGE = "No documents are in scope for this request, so there is nothing to answer from."


def _rewrite_record(result: RewriteResult | None) -> RewriteRecord | None:
    """The rewrite step's verdict, in the shape the audit row stores."""
    if result is None:
        return None
    return RewriteRecord(
        original=result.original,
        searched_for=result.question,
        rewritten=result.rewritten,
        source=result.source,
        reason=result.reason,
    )


@dataclass(frozen=True)
class _PreparedRewriter:
    """A rewriter that has already run.

    The workflows call `rewrite()` synchronously inside their async `run()`. This
    satisfies that call with a result computed in a worker thread beforehand, which
    keeps a blocking vendor call off the event loop without changing the workflow's
    shape. See the module docstring.
    """

    result: RewriteResult

    def rewrite(self, question: str, history: Any = None) -> RewriteResult:
        return self.result


@dataclass(frozen=True)
class _OwnerScoped:
    """A retriever bound to one owner, for the workflows to call.

    The workflows take a retriever and ask it for passages; they know nothing about
    tenancy and should not have to. This binds the owner once, at the edge, so every
    call a workflow makes carries it — rather than each workflow remembering to pass
    an owner it has no other use for. A workflow that forgot would silently fall back
    to filtering on document ids alone.
    """

    retrieval: Any
    doc_ids: set[str]
    owner_id: str

    async def select(self, question: str, *, doc_ids=None, top_n=None):
        return await self.retrieval.select(
            question,
            doc_ids=doc_ids or self.doc_ids,
            owner_id=self.owner_id,
            top_n=top_n,
        )

    async def search(self, question: str, *, doc_ids=None, top_n=None):
        return await self.retrieval.search(
            question,
            doc_ids=doc_ids or self.doc_ids,
            owner_id=self.owner_id,
            top_n=top_n,
        )

    async def whole(self, doc_ids=None):
        return await self.retrieval.whole(doc_ids or self.doc_ids, self.owner_id)


class OrchestratorProvider:
    """Streams a cited answer by running one of the three workflows."""

    name = "orchestrator"
    #: See the module docstring: coverage and page selection are one decision.
    owns_retrieval = True

    def __init__(
        self,
        settings: Settings,
        retrieval: OrchestratedRetrieval,
        *,
        client: Any = None,
        rewriter: Any = None,
    ) -> None:
        # Both seams are injected by the tests, so the dispatch and the translation
        # can be exercised without a vendor call or an API key — the same convention
        # the three workflows use.
        if client is None and not settings.anthropic_api_key:
            raise RuntimeError(
                "llm_provider='orchestrator' needs LEGAL_LENS_ANTHROPIC_API_KEY; "
                "set it or use llm_provider='echo'"
            )
        self._settings = settings
        self._retrieval = retrieval
        self._config = retrieval.config

        if client is None:
            from ..orchestrator.workflows.generate import build_client

            # One client for all three workflows. They differ in model and effort,
            # which are per-call arguments, not per-connection.
            client = build_client(self._config.require_api_key(), self._config.headers)
        self._client = client
        self._rewriter: Any = rewriter

    # ------------------------------------------------------------------ parts

    async def _rewrite(self, question: str, request: GenerationRequest) -> RewriteResult:
        """Resolve the question against the history, in a worker thread."""
        from ..orchestrator.workflows import QueryRewriter, RewriteResult

        if not request.history:
            # The rewriter's own first guardrail, applied before anything is built:
            # with nothing to resolve a reference against there is no call to make,
            # and the first turn of every conversation takes this path. Constructing
            # a LangChain client to be told that is a cost with no reader.
            return RewriteResult(
                question=question,
                original=question,
                rewritten=False,
                reason="No conversation history, so nothing to resolve against.",
                source="empty_history",
            )

        if self._rewriter is None:
            # Constructing this builds a LangChain client, so it is deferred to the
            # first turn that has history to resolve rather than done at boot.
            self._rewriter = await asyncio.to_thread(QueryRewriter, self._config)
        return await asyncio.to_thread(
            self._rewriter.rewrite, question, to_orch_turns(request.history)
        )

    def _usages(self, calls: list[Any], intent: str) -> list[Usage]:
        """Orchestrator usage rows as the cost table records them.

        The generation call is renamed to `generation:<intent>` so its rows line up
        with the ones the other providers write; every other step keeps the name the
        orchestrator gave it, because `rewrite` is exactly what it is.
        """
        out: list[Usage] = []
        for call in calls:
            purpose = call.purpose
            if purpose == intent:
                purpose = f"generation:{intent}"
            elif purpose.startswith(f"{intent}:"):
                purpose = f"generation:{purpose}"
            out.append(to_usage(call, purpose=purpose))
        return out

    @staticmethod
    def _headline(usages: list[Usage], intent: str) -> Usage:
        """The call that produced the answer, for the audit row's `model_id`.

        Not the last call and not the most expensive: the row should name the model
        that wrote the answer, not the Haiku that tidied the question.
        """
        for usage in usages:
            if usage.purpose == f"generation:{intent}":
                return usage
        return usages[-1] if usages else Usage()

    def model_for(self, intent: str) -> str:
        """The model this provider would use for `intent`.

        Asked by the runner when a turn was stopped or failed before any usage was
        reported, so the audit row names a model rather than nothing. Each workflow
        owns its own tier, which is why the single `generation_model` setting cannot
        answer this.

        Summary is the imprecise one: it routes to the larger model on two or more
        documents or on an amendment, and that is decided from the pages, which a
        turn that died early never selected. The smaller model is reported as the
        lower bound. No cost row is written in this case — only the model name — so
        the imprecision does not reach the billing figures.
        """
        if intent == "summary":
            return self._config.summary_model_small
        if intent == "draft":
            return self._config.draft_model
        return self._config.ask_model

    # ----------------------------------------------------------------- stream

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        doc_ids = set(request.scoped_document_ids)
        if not doc_ids:
            # Nothing to search. An abstention, not an empty search — see the owner
            # scoping note in `retrieval/orchestrated.py`.
            yield Abstained(NO_SCOPE_MESSAGE)
            yield Finished(usage=Usage(purpose=f"generation:{request.intent}"))
            return

        # The bridge, not a bare retriever: it is what requires a non-empty document
        # scope and a non-empty owner before anything is searched, and what puts the
        # owner filter on the query. Handing the workflows a raw retriever would make
        # that check skippable.
        retriever = _OwnerScoped(self._retrieval, doc_ids, request.owner_id)

        if request.intent == "summary":
            async for event in self._summary(request, doc_ids, retriever):
                yield event
        elif request.intent == "draft":
            async for event in self._draft(request, doc_ids, retriever):
                yield event
        else:
            async for event in self._ask(request, doc_ids, retriever):
                yield event

    # -------------------------------------------------------------------- ask

    async def _ask(
        self, request: GenerationRequest, doc_ids: set[str], retriever: Any
    ) -> AsyncIterator[GenerationEvent]:
        from ..orchestrator.workflows import Abstained as OrchAbstained
        from ..orchestrator.workflows import AskResult, AskWorkflow
        from ..orchestrator.workflows import CitationFound as OrchCitationFound
        from ..orchestrator.workflows import TextDelta as OrchTextDelta

        workflow = AskWorkflow(
            self._config,
            retriever,
            rewriter=_PreparedRewriter(await self._rewrite(request.question, request)),
            client=self._client,
        )

        result: AskResult | None = None
        async for event in workflow.run(
            request.question,
            # Ignored by the prepared rewriter, which has already run. Passed anyway:
            # the call should not be silently wrong if that indirection ever goes.
            history=to_orch_turns(request.history),
            doc_ids=doc_ids,
        ):
            if isinstance(event, OrchTextDelta):
                yield TextDelta(text=event.text)
            elif isinstance(event, OrchCitationFound):
                yield CitationFound(citation=to_wire_citation(event.citation))
            elif isinstance(event, OrchAbstained):
                yield Abstained(event.message)
            elif isinstance(event, AskResult):
                result = event

        if result is None:  # pragma: no cover — the workflow always yields one last
            log.error("ask workflow produced no result")
            yield Finished(usage=Usage(purpose="generation:ask"))
            return

        usages = self._usages(result.usage, "ask")
        log.info(
            "ask served",
            extra={
                "rewritten": result.rewrite.rewritten if result.rewrite else False,
                "searched_for": result.searched_for,
                "pages": result.pages,
                "candidates": result.candidates,
                "coverage": result.coverage,
                "citations": len(result.citations),
                "abstained": result.abstained,
            },
        )
        yield Finished(
            usage=self._headline(usages, "ask"),
            calls=usages,
            retrieval=RetrievalReport(
                # Reported, not assumed: `ask` reads the whole corpus when it fits
                # the whole-document budget and retrieves when it does not.
                path=result.path,
                document_ids=sorted(doc_ids),
                pages=result.pages,
                coverage=result.coverage,
                rewrite=_rewrite_record(result.rewrite),
            ),
        )

    # ---------------------------------------------------------------- summary

    async def _summary(
        self, request: GenerationRequest, doc_ids: set[str], retriever: Any
    ) -> AsyncIterator[GenerationEvent]:
        from ..orchestrator.workflows import Abstained as OrchAbstained
        from ..orchestrator.workflows import CitationFound as OrchCitationFound
        from ..orchestrator.workflows import SummaryResult, SummaryWorkflow

        workflow = SummaryWorkflow(self._config, retriever, client=self._client)

        result: SummaryResult | None = None
        async for event in workflow.run(doc_ids=doc_ids, request=request.question):
            # Text is deliberately dropped — see the module docstring.
            if isinstance(event, OrchCitationFound):
                yield CitationFound(citation=to_wire_citation(event.citation))
            elif isinstance(event, OrchAbstained):
                yield Abstained(event.message)
            elif isinstance(event, SummaryResult):
                result = event

        if result is None:  # pragma: no cover
            log.error("summary workflow produced no result")
            yield Finished(usage=Usage(purpose="generation:summary"))
            return

        if not result.abstained:
            yield Structured(summary=[to_wire_summary_field(f) for f in result.fields])

        usages = self._usages(result.usage, "summary")
        log.info(
            "summary served",
            extra={
                "model": result.model,
                "documents": result.documents,
                "pages": result.pages,
                "retried": result.retried,
                "missing": result.missing,
                "citations": len(result.citations),
            },
        )
        yield Finished(
            usage=self._headline(usages, "summary"),
            calls=usages,
            retrieval=RetrievalReport(
                # The summary reads every page in scope; that is what a summary is.
                path="whole_document",
                document_ids=sorted(doc_ids),
                pages=result.pages,
                coverage=True,
            ),
        )

    # ------------------------------------------------------------------ draft

    async def _draft(
        self, request: GenerationRequest, doc_ids: set[str], retriever: Any
    ) -> AsyncIterator[GenerationEvent]:
        from ..orchestrator.workflows import Abstained as OrchAbstained
        from ..orchestrator.workflows import CitationFound as OrchCitationFound
        from ..orchestrator.workflows import DraftResult, DraftWorkflow

        workflow = DraftWorkflow(
            self._config,
            retriever,
            rewriter=_PreparedRewriter(await self._rewrite(request.question, request)),
            client=self._client,
        )

        result: DraftResult | None = None
        async for event in workflow.run(
            request.question,
            history=to_orch_turns(request.history),
            doc_ids=doc_ids,
        ):
            # Text is deliberately dropped — see the module docstring.
            if isinstance(event, OrchCitationFound):
                yield CitationFound(citation=to_wire_citation(event.citation))
            elif isinstance(event, OrchAbstained):
                yield Abstained(event.message)
            elif isinstance(event, DraftResult):
                result = event

        if result is None:  # pragma: no cover
            log.error("draft workflow produced no result")
            yield Finished(usage=Usage(purpose="generation:draft"))
            return

        if not result.abstained:
            yield Structured(draft=DraftPayload(title=result.title or "Draft", body=result.body))

        usages = self._usages(result.usage, "draft")
        log.info(
            "draft served",
            extra={
                "searched_for": result.searched_for,
                "pages": result.pages,
                "coverage": result.coverage,
                "markers": result.markers,
                "citations": len(result.citations),
            },
        )
        yield Finished(
            usage=self._headline(usages, "draft"),
            calls=usages,
            retrieval=RetrievalReport(
                path=result.path,
                document_ids=sorted(doc_ids),
                pages=result.pages,
                coverage=result.coverage,
                rewrite=_rewrite_record(result.rewrite),
            ),
        )
