"""One assistant turn, from pending registration to persisted message.

The event sequence this produces is the protocol the frontend already reads
(`lib/sse.ts`), and two properties of it are load-bearing:

1. **`meta` precedes every token, and is re-sent whenever a citation arrives.** The
   renderer resolves `{{cite:n}}` markers against `meta.citations`, so a marker that
   reached the client before its citation would render as a dead chip. Re-sending
   `meta` is safe because the frontend replaces its stored copy wholesale.
2. **The turn is persisted with its citations in one transaction, at the end.** A
   message whose citations failed to write renders as an uncited factual assertion,
   which NFR-1 forbids.

A provider that owns its retrieval (`owns_retrieval`, see `ai/types.py`) is handed a
document scope instead of a page list, and reports back what it read. Selection is
skipped for it rather than computed and discarded — and the audit row is built from
the provider's report, so it describes the request that happened rather than a
selection nothing used.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..ai.adapt import history_from_messages
from ..ai.pricing import cost_usd
from ..ai.types import (
    Abstained,
    CitationFound,
    Finished,
    GenerationProvider,
    GenerationRequest,
    HistoryTurn,
    RetrievalPath,
    RewriteRecord,
    SourcePage,
    Structured,
    TextDelta,
    Usage,
)
from ..clock import utcnow
from ..observability import METRICS
from ..retrieval import select_pages
from ..schemas import Citation, DraftPayload, SummaryField
from .registry import PendingTurn

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """What was produced, so it can be persisted and audited after the stream."""

    content: str = ""
    citations: list[Citation] = field(default_factory=list)
    summary: list[SummaryField] | None = None
    draft: DraftPayload | None = None
    abstained: bool = False
    grounded: bool = True
    usage: Usage = field(default_factory=Usage)
    #: Every model call the turn made. One for most providers; a workflow that
    #: rewrites the question before answering spends on two tiers and gets two rows,
    #: because "what is the rewrite step costing us?" is otherwise unanswerable.
    calls: list[Usage] = field(default_factory=list)
    #: Provisional until the provider reports; see `Finished.retrieval`.
    retrieval_path: RetrievalPath = "rag"
    document_ids: list[str] = field(default_factory=list)
    #: Whether the model was shown every page in scope. `None` when the provider did
    #: not say — which is not the same as `False`, and an audit row that cannot
    #: distinguish "partial" from "unrecorded" is misleading about the one case where
    #: an absence claim would have been unsafe.
    coverage: bool | None = None
    #: The pre-retrieval rewrite, when the provider ran one.
    rewrite: RewriteRecord | None = None
    outcome: str = "ok"

    @property
    def produced_anything(self) -> bool:
        """Whether this turn has something to show a reader.

        Citations alone do not count: a turn can collect sources and still end with
        no prose and no payload, and a list of sources for an answer that is not
        there is the shape of the failure, not a mitigation of it.
        """
        return bool(self.content.strip() or self.summary is not None or self.draft is not None)


class TurnRunner:
    def __init__(self, services: Any) -> None:
        self._services = services

    async def run(
        self, turn: PendingTurn, provider: GenerationProvider
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield `(event_name, payload)` pairs for the SSE route to serialise."""
        started = utcnow()
        result = TurnResult()

        # A provider that retrieves for itself gets a scope and the conversation so
        # far; everything else gets pages this runner chose. Selection is not run for
        # the former: it would load and embed the whole corpus to produce a page list
        # the provider discards, and `select_pages` is the slow part of this path.
        # `getattr` rather than attribute access: the protocol is structural, so a
        # provider written before this flag existed still has to work.
        owns_retrieval = getattr(provider, "owns_retrieval", False)
        pages: list[SourcePage] = []
        history: list[HistoryTurn] = []

        if owns_retrieval:
            result.document_ids = await self._scope(turn)
            history = await self._history(turn)
        else:
            selection = await self._select(turn)
            pages = selection.pages
            result.retrieval_path = selection.path
            result.document_ids = sorted({p.document_id for p in selection.pages})

        # Sent before any token, and again after each citation.
        meta: dict[str, Any] = {
            "id": turn.message_id,
            "intent": turn.intent,
            "grounded": True,
            "abstained": False,
            "citations": [],
        }
        yield "meta", dict(meta)

        request = GenerationRequest(
            question=turn.question,
            intent=turn.intent,
            pages=pages,
            retrieval_path=result.retrieval_path,
            scoped_document_ids=result.document_ids,
            owner_id=turn.owner_id,
            history=history,
        )

        try:
            async for event in provider.stream(request):
                if turn.cancel.is_set():
                    # The user pressed stop. The partial text is kept — it is what
                    # they saw — and the audit row says `stopped` rather than `ok`.
                    result.outcome = "stopped"
                    break

                if isinstance(event, TextDelta):
                    result.content += event.text
                    yield "token", {"text": event.text}

                elif isinstance(event, CitationFound):
                    result.citations.append(event.citation)
                    meta["citations"] = [c.model_dump(by_alias=True) for c in result.citations]
                    # Before the marker, never after.
                    yield "meta", dict(meta)

                elif isinstance(event, Structured):
                    result.summary = event.summary
                    result.draft = event.draft
                    if event.summary is not None:
                        meta["summary"] = [f.model_dump(by_alias=True) for f in event.summary]
                    if event.draft is not None:
                        meta["draft"] = event.draft.model_dump(by_alias=True)
                    yield "meta", dict(meta)

                elif isinstance(event, Abstained):
                    # FR-9: a correct outcome, not an error. The notice is the body.
                    result.abstained = True
                    result.grounded = True
                    result.content = event.message
                    meta["abstained"] = True
                    yield "meta", dict(meta)
                    yield "token", {"text": event.message}

                elif isinstance(event, Finished):
                    result.usage = event.usage
                    # `calls` is empty for a provider that makes exactly one call.
                    result.calls = list(event.calls) or [event.usage]
                    if event.retrieval is not None:
                        # The provider chose the pages, so only it can say what was
                        # read. Replaces the provisional values set above.
                        result.retrieval_path = event.retrieval.path
                        result.document_ids = event.retrieval.document_ids
                        result.coverage = event.retrieval.coverage
                        result.rewrite = event.retrieval.rewrite

        except Exception:
            log.exception("generation failed", extra={"message_id": turn.message_id})
            result.outcome = "error"
            METRICS.incr("generation", outcome="error")
            # The stream is already open, so the failure has to travel as an event
            # rather than a status code. `done` still fires in the route's `finally`,
            # so the UI stops spinning instead of hanging.
            yield (
                "error",
                {
                    "message": "Generation failed before it completed.",
                    "requestId": turn.message_id,
                },
            )

        # A factual answer with no citation is ungrounded by definition (NFR-1).
        # Abstention and structured payloads are judged on their own terms.
        if not result.abstained and result.summary is None and result.draft is None:
            result.grounded = bool(result.citations)

        # A turn that produced nothing at all. It happens when the provider's stream
        # ends without yielding its result — a cancelled generation, a workflow that
        # returned early — and until this existed it persisted as a normal `ok` turn
        # with empty content, which the transcript rendered as a blank bubble under an
        # intent badge. The outcome is recorded so the audit row and the transcript
        # agree about what happened, and so "how often does this fire?" is answerable
        # from the table rather than from a user's report.
        if result.outcome == "ok" and not result.produced_anything:
            result.outcome = "empty"
            log.warning(
                "generation produced nothing",
                extra={
                    "message_id": turn.message_id,
                    "intent": turn.intent,
                    "citations": len(result.citations),
                },
            )
            METRICS.incr("generation", outcome="empty")

        # A stopped or failed turn never reaches the provider's `Finished` event, so
        # its token counts are unknown rather than zero. Record which model was
        # running — otherwise the audit row names no model at all — but write no cost
        # row, because a $0 row reads as "this was free" rather than "unmeasured".
        if not result.usage.model_id:
            # A provider that picks a model per intent is asked which one; the single
            # `generation_model` setting would name the wrong model for two of three.
            names = getattr(provider, "model_for", None)
            if names is not None:
                result.usage.model_id = names(turn.intent)
            elif provider.name == "echo":
                result.usage.model_id = "echo-stub"
            else:
                result.usage.model_id = getattr(self._services.settings, "generation_model", "")

        latency_ms = int((utcnow() - started).total_seconds() * 1000)
        await self._persist(turn, result, latency_ms)

        METRICS.incr("generation", outcome=result.outcome, intent=turn.intent)
        METRICS.observe("generation_ms", latency_ms, intent=turn.intent)
        # `done` is emitted by the route's `finally`, not here: it has to fire even
        # when this generator raises or the client disconnects, and emitting it in
        # both places sent it twice.

    # ------------------------------------------------------------------ parts

    async def _scope(self, turn: PendingTurn) -> list[str]:
        """The documents a self-retrieving provider may read.

        The one place that scope is decided, and it comes from an owner-filtered
        query. That matters more than it looks: the orchestrator's Qdrant payload
        carries no owner and filters by document id, so this list is what stops a
        question reaching another owner's pages. See `retrieval/orchestrated.py`.
        """
        documents = await self._services.documents.list(turn.owner_id)
        ready = [d.id for d in documents if d.status == "ready"]
        if turn.scoped_document_ids:
            wanted = set(turn.scoped_document_ids)
            ready = [d for d in ready if d in wanted]
        return sorted(ready)

    async def _history(self, turn: PendingTurn) -> list[HistoryTurn]:
        """The conversation so far, for the query-rewrite step.

        The turn being answered is already in the table — `POST /chat/message` writes
        the user message before the stream opens — so it is dropped here. Passing the
        rewrite step the question it was asked to resolve, labelled as context, is an
        invitation to resolve the question against itself.
        """
        messages = await self._services.messages.list_for_conversation(
            turn.owner_id, turn.conversation_id
        )
        if messages and messages[-1].role == "user" and messages[-1].content == turn.question:
            messages = messages[:-1]
        return history_from_messages(messages)

    async def _select(self, turn: PendingTurn):

        services = self._services
        documents = await services.documents.list(turn.owner_id)
        ready = [d for d in documents if d.status == "ready"]

        if turn.scoped_document_ids:
            wanted = set(turn.scoped_document_ids)
            ready = [d for d in ready if d.id in wanted]

        rows: list[tuple[str, str, int]] = []
        pages_by_document: dict[str, list[tuple[int, str]]] = {}
        for document in ready:
            page_rows = await services.documents.all_pages(turn.owner_id, document.id)
            if not page_rows:
                continue
            rows.append((document.id, document.title, sum(len(t) for _, t in page_rows) // 4))
            pages_by_document[document.id] = page_rows

        return select_pages(
            question=turn.question,
            documents=rows,
            pages_by_document=pages_by_document,
            settings=services.settings,
            embedder=services.embedder,
            store=services.vector_store,
            owner_id=turn.owner_id,
        )

    async def _persist(self, turn, result, latency_ms) -> None:
        services = self._services

        await services.messages.persist_assistant_turn(
            owner_id=turn.owner_id,
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            content=result.content,
            intent=turn.intent,
            citations=result.citations,
            grounded=result.grounded,
            abstained=result.abstained,
            summary=result.summary,
            draft=result.draft,
        )
        await services.conversations.touch(turn.owner_id, turn.conversation_id, turn.intent)

        # One audit row for the turn, one cost row per model call. A turn that
        # rewrote its question spent on two models, so the row's totals are the sum
        # across calls while `model_id` names the one that wrote the answer —
        # otherwise a turn's cost would read as whatever its last call happened to be.
        calls = result.calls or [result.usage]
        spends = [
            cost_usd(
                call.model_id,
                call.input_tokens,
                call.output_tokens,
                call.cache_read_tokens,
                call.cache_write_tokens,
            )
            for call in calls
        ]
        spend = sum(spends)
        audit_id = await services.audit.record(
            owner_id=turn.owner_id,
            feature=turn.intent,
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            question=turn.question,
            document_ids=result.document_ids,
            # The spans that grounded the answer: the record of what the model was
            # shown and used, which is the part that matters when an answer is
            # challenged (FR-20).
            grounding_spans=result.citations,
            retrieval_path=result.retrieval_path,
            coverage=result.coverage,
            rewrite=result.rewrite,
            model_id=result.usage.model_id,
            input_tokens=sum(c.input_tokens for c in calls),
            output_tokens=sum(c.output_tokens for c in calls),
            cache_read_tokens=sum(c.cache_read_tokens for c in calls),
            cache_write_tokens=sum(c.cache_write_tokens for c in calls),
            cost_usd=spend,
            latency_ms=latency_ms,
            abstained=result.abstained,
            grounded=result.grounded,
            outcome=result.outcome,
        )
        # Only calls whose tokens were actually measured. See the note in `run`: a
        # $0 row reads as "this was free" rather than "unmeasured".
        for call, call_spend in zip(calls, spends, strict=True):
            if not call.model_id or not (call.input_tokens or call.output_tokens):
                continue
            await services.audit.record_cost(
                owner_id=turn.owner_id,
                audit_id=audit_id,
                purpose=call.purpose or f"generation:{turn.intent}",
                model_id=call.model_id,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cache_read_tokens=call.cache_read_tokens,
                cache_write_tokens=call.cache_write_tokens,
                cost_usd=call_spend,
            )
        METRICS.incr("tokens_input", sum(c.input_tokens for c in calls))
        METRICS.incr("tokens_output", sum(c.output_tokens for c in calls))
        METRICS.incr("cost_usd", spend)
