"""The orchestration layer behind the generation seam.

Everything here is offline. What is being tested is the integration itself — the
translation between the orchestrator's dataclasses and the wire types, which intent
reaches which workflow, what a self-retrieving provider reports back for the audit
row, and the two contract points that fail quietly if they drift:

- a summary or draft streams **no** text, because its payload renders as a card and a
  half-written one reads as broken;
- an empty document scope **abstains** rather than searching every document, which is
  what keeps a question inside its owner's corpus.

Whether the model obeys the prompts is measured by the CLI against the eval corpus.
"""

from __future__ import annotations

from typing import Any

import pytest

from legal_lens.ai.adapt import history_from_messages
from legal_lens.ai.orchestrator_provider import OrchestratorProvider
from legal_lens.ai.types import (
    Abstained,
    CitationFound,
    Finished,
    GenerationRequest,
    HistoryTurn,
    Structured,
    TextDelta,
)
from legal_lens.config import Settings
from legal_lens.orchestrator.config import OrchestratorConfig
from legal_lens.orchestrator.workflows import MARKER
from test_orchestrator_workflows import (
    PAGES,
    FakeClient,
    FakeFinal,
    FakeRetriever,
    NoRewrite,
    block_stop,
    citation_event,
    text_events,
)

SCOPE = ["msa", "nda"]

HEADINGS = (
    "Parties",
    "Term & renewal",
    "Obligations",
    "Payment terms",
    "Termination triggers",
    "Governing law & venue",
    "Liability & indemnification",
    "Unusual provisions",
    "Open items",
)


def summary_body(*labels: str) -> str:
    """A `## Heading` response. Omitting headings is what triggers the retry pass."""
    return "\n".join(
        f"## {label}\nSomething about {label.lower()}." for label in (labels or HEADINGS)
    )


class FakeRetrieval:
    """Stands in for `OrchestratedRetrieval`: a config and the three read methods.

    It mirrors the real surface rather than only `retriever()`, because the provider
    binds an owner to that surface before the workflows see it — so the owner is
    recorded here, and `test_the_owner_travels_into_every_retrieval_call` asserts it
    arrives.
    """

    def __init__(self, retriever: Any, config: OrchestratorConfig | None = None) -> None:
        self.config = config or OrchestratorConfig(api_key="test-key")
        self._retriever = retriever
        #: Every `(method, doc_ids, owner_id)` this was asked for.
        self.scopes: list[tuple[str, set[str], str]] = []

    async def retriever(self) -> Any:
        return self._retriever

    async def select(self, question, *, doc_ids, owner_id, top_n=None):
        self.scopes.append(("select", set(doc_ids), owner_id))
        return await self._retriever.select(question, doc_ids=doc_ids, top_n=top_n)

    async def search(self, question, *, doc_ids, owner_id, top_n=None):
        self.scopes.append(("search", set(doc_ids), owner_id))
        return await self._retriever.search(question, doc_ids=doc_ids, top_n=top_n)

    async def whole(self, doc_ids, owner_id):
        self.scopes.append(("whole", set(doc_ids), owner_id))
        return await self._retriever.whole(doc_ids)


def _provider(client: Any, *, pages=None, corpus_pages: int = 3, config=None):
    retriever = FakeRetriever(pages if pages is not None else PAGES, corpus_pages=corpus_pages)
    retrieval = FakeRetrieval(retriever, config)
    provider = OrchestratorProvider(
        Settings(environment="test", llm_provider="orchestrator"),
        retrieval,  # type: ignore[arg-type]
        client=client,
        rewriter=NoRewrite(),
    )
    # The bridge is reachable from the retriever so existing tests keep their
    # two-value unpacking while new ones can assert on the scope it was handed.
    retriever.retrieval = retrieval  # type: ignore[attr-defined]
    return provider, retriever


def _request(intent: str, question: str = "Does the MSA cap liability?", **over):
    return GenerationRequest(
        question=question,
        intent=intent,  # type: ignore[arg-type]
        pages=[],
        retrieval_path="rag",
        scoped_document_ids=over.pop("scope", list(SCOPE)),
        owner_id=over.pop("owner_id", "owner-local"),
        history=over.pop("history", []),
    )


async def _collect(provider: OrchestratorProvider, request: GenerationRequest):
    events: list[Any] = []
    async for event in provider.stream(request):
        events.append(event)
    return events


def _only(events: list[Any], kind) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


async def test_ask_streams_text_and_translates_citations_to_the_wire_shape() -> None:
    client = FakeClient(
        (
            [
                # The real event order: a block's citation arrives before its text.
                citation_event(0, 1, "Page two. Indemnity."),
                *text_events("Liability is capped ", "at fees paid."),
                block_stop(),
            ],
            FakeFinal(),
        )
    )
    provider, _ = _provider(client)

    events = await _collect(provider, _request("ask"))

    # The marker travels with the prose, so the chat renderer can put a chip against
    # the claim rather than only counting sources in the header.
    assert "".join(e.text for e in _only(events, TextDelta)) == (
        "Liability is capped at fees paid.{{cite:1}}"
    )
    citations = [e.citation for e in _only(events, CitationFound)]
    assert len(citations) == 1
    # The orchestrator says doc_id/doc_title; the wire says documentId/documentTitle.
    # This is the translation that silently produces a chip pointing nowhere.
    assert citations[0].document_id == "msa"
    assert citations[0].document_title == "Master Services Agreement"
    assert citations[0].page == 2
    assert citations[0].cited_text == "Page two. Indemnity."
    assert citations[0].n == 1


async def test_ask_reports_what_retrieval_read_for_the_audit_row() -> None:
    client = FakeClient((text_events("An answer."), FakeFinal()))
    # Three pages selected out of a ten-page corpus: a partial read, so an absence
    # was not provable and the audit row should say so.
    provider, _ = _provider(client, corpus_pages=10)

    events = await _collect(provider, _request("ask"))
    finished = _only(events, Finished)[-1]

    assert finished.retrieval is not None
    assert finished.retrieval.path == "rag"
    assert finished.retrieval.pages == 3
    assert finished.retrieval.coverage is False
    assert finished.retrieval.document_ids == SCOPE


async def test_full_coverage_is_reported_when_every_page_was_read() -> None:
    client = FakeClient((text_events("No such clause."), FakeFinal()))
    provider, _ = _provider(client, corpus_pages=3)

    events = await _collect(provider, _request("ask"))

    assert _only(events, Finished)[-1].retrieval.coverage is True


async def test_an_abstention_travels_as_an_abstention() -> None:
    client = FakeClient((text_events("NOT_IN_DOCUMENTS"), FakeFinal()))
    provider, _ = _provider(client)

    events = await _collect(provider, _request("ask"))

    abstentions = _only(events, Abstained)
    assert len(abstentions) == 1
    # The sentinel itself must never reach the transcript.
    assert "NOT_IN_DOCUMENTS" not in abstentions[0].message
    assert not _only(events, TextDelta)


# ---------------------------------------------------------------------------
# summary and draft: structured, and silent while they work
# ---------------------------------------------------------------------------


async def test_summary_streams_no_text_and_resolves_as_one_payload() -> None:
    client = FakeClient(
        (
            [*text_events(summary_body()), citation_event(0, 0, "Page one. Parties.")],
            FakeFinal(),
        )
    )
    provider, _ = _provider(client)

    events = await _collect(provider, _request("summary", "Summarise the MSA"))

    # The contract point: a summary message carries `content: ""` and renders from
    # its fields. Streaming the prose would put a half-built table in the transcript.
    assert not _only(events, TextDelta)
    # Citations still stream, because the chips are real.
    assert len(_only(events, CitationFound)) == 1

    structured = _only(events, Structured)
    assert len(structured) == 1
    fields = structured[0].summary
    assert fields is not None
    assert len(fields) == 9
    assert [f.label for f in fields][0] == "Parties"
    assert all(f.value for f in fields)


async def test_summary_is_reported_as_a_whole_document_read() -> None:
    client = FakeClient((text_events(summary_body()), FakeFinal()))
    provider, _ = _provider(client, corpus_pages=3)

    events = await _collect(provider, _request("summary", "Summarise"))
    finished = _only(events, Finished)[-1]

    # A summary reads everything in scope; that is what a summary is.
    assert finished.retrieval.path == "whole_document"
    assert finished.retrieval.coverage is True


async def test_a_skipped_heading_is_refilled_and_billed_as_its_own_call() -> None:
    """The retry is a second model call, and the prose it re-emits is not streamed.

    `_fill` re-answers the headings the first pass skipped, repeating them. Streaming
    both passes would append a second copy of those headings to the transcript — the
    other reason summary sends no text.
    """
    client = FakeClient(
        # First pass skips six of the nine headings.
        (text_events(summary_body("Parties", "Term & renewal", "Obligations")), FakeFinal()),
        # The retry supplies them.
        (text_events(summary_body(*HEADINGS[3:])), FakeFinal()),
    )
    provider, _ = _provider(client)

    events = await _collect(provider, _request("summary", "Summarise"))

    assert not _only(events, TextDelta)
    fields = _only(events, Structured)[0].summary
    assert fields is not None
    # All nine present and filled, across the two passes.
    assert [f.label for f in fields] == list(HEADINGS)
    assert all(f.value for f in fields)

    purposes = [c.purpose for c in _only(events, Finished)[-1].calls]
    assert purposes == ["generation:summary", "generation:summary:retry"]


async def test_draft_streams_no_text_and_keeps_its_marker_intact() -> None:
    body = f"## Memorandum\n\nThe term is three years.\n\n{MARKER} — confirm venue."
    client = FakeClient((text_events(body), FakeFinal()))
    provider, _ = _provider(client)

    events = await _collect(provider, _request("draft", "Draft a memo on venue"))

    assert not _only(events, TextDelta)
    structured = _only(events, Structured)
    assert len(structured) == 1
    draft = structured[0].draft
    assert draft is not None
    # FR-16: the marker is styled on render and must survive untouched.
    assert MARKER in draft.body
    assert draft.title


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected_method"),
    [("ask", "select"), ("draft", "select"), ("summary", "whole")],
)
async def test_the_owner_travels_into_every_retrieval_call(
    intent: str, expected_method: str
) -> None:
    """Every workflow's retrieval is bound to the requesting owner.

    The workflows know nothing about tenancy — they ask a retriever for passages. The
    owner is bound once at the provider edge so none of them has to remember it, and
    this asserts the binding rather than the remembering.
    """
    body = summary_body() if intent == "summary" else "An answer."
    provider, retriever = _provider(FakeClient((text_events(body), FakeFinal())))

    await _collect(provider, _request(intent, owner_id="owner-zed"))

    scopes = retriever.retrieval.scopes
    assert scopes, "retrieval was never called"
    methods = {method for method, _, _ in scopes}
    assert expected_method in methods
    # Not one call went out without the owner.
    assert all(owner == "owner-zed" for _, _, owner in scopes)


async def test_an_empty_scope_abstains_rather_than_searching_everything() -> None:
    client = FakeClient()  # no scripted response: nothing should be called
    provider, retriever = _provider(client)

    events = await _collect(provider, _request("ask", scope=[]))

    assert len(_only(events, Abstained)) == 1
    assert not _only(events, TextDelta)
    # The guarantee: no search was attempted. An unscoped search would reach every
    # owner's documents, because the index filters by document id alone.
    assert retriever.searched == []


async def test_the_scope_is_passed_through_to_retrieval() -> None:
    client = FakeClient((text_events("Answer."), FakeFinal()))
    provider, retriever = _provider(client)

    await _collect(provider, _request("ask", scope=["nda"]))

    assert retriever.searched == ["Does the MSA cap liability?"]


# ---------------------------------------------------------------------------
# cost, per call
# ---------------------------------------------------------------------------


async def test_each_model_call_is_billed_separately_and_the_answer_names_the_model() -> None:
    """A turn that rewrites its question spends on two tiers.

    Billing them as one row loses which was which, which is the question
    `Usage.purpose` exists to answer.
    """
    from legal_lens.orchestrator.types import Usage as OrchUsage
    from legal_lens.orchestrator.workflows.rewrite import RewriteResult

    class Rewriter:
        def rewrite(self, question, history=None):
            return RewriteResult(
                question="Does the Master Services Agreement cap liability?",
                original=question,
                rewritten=True,
                reason="resolved 'it' to the MSA",
                source="model",
                usage=OrchUsage(
                    model_id="claude-haiku-4-5",
                    input_tokens=120,
                    output_tokens=20,
                    purpose="rewrite",
                ),
            )

    client = FakeClient((text_events("Capped at fees paid."), FakeFinal()))
    retriever = FakeRetriever(PAGES, corpus_pages=3)
    provider = OrchestratorProvider(
        Settings(environment="test", llm_provider="orchestrator"),
        FakeRetrieval(retriever),  # type: ignore[arg-type]
        client=client,
        rewriter=Rewriter(),
    )

    events = await _collect(
        provider,
        _request("ask", history=[HistoryTurn(role="user", content="Tell me about the MSA.")]),
    )
    finished = _only(events, Finished)[-1]

    purposes = [c.purpose for c in finished.calls]
    assert purposes == ["rewrite", "generation:ask"]
    assert [c.model_id for c in finished.calls][0] == "claude-haiku-4-5"
    # The headline names what wrote the answer, not the Haiku that tidied the question.
    assert finished.usage.purpose == "generation:ask"
    # And the rewrite actually reached retrieval.
    assert retriever.searched == ["Does the Master Services Agreement cap liability?"]


async def test_a_first_turn_needs_no_rewriter_at_all() -> None:
    """With no history there is nothing to resolve, so nothing is constructed.

    `QueryRewriter.__init__` builds a LangChain client and demands an API key; the
    first turn of every conversation would otherwise pay for it to be told there is
    no history.
    """
    client = FakeClient((text_events("Answer."), FakeFinal()))
    retriever = FakeRetriever(PAGES, corpus_pages=3)
    provider = OrchestratorProvider(
        Settings(environment="test", llm_provider="orchestrator"),
        FakeRetrieval(retriever),  # type: ignore[arg-type]
        client=client,
        # Deliberately absent: constructing one would raise without a key.
        rewriter=None,
    )

    events = await _collect(provider, _request("ask", history=[]))

    assert _only(events, Finished)
    assert [c.purpose for c in _only(events, Finished)[-1].calls] == ["generation:ask"]


# ---------------------------------------------------------------------------
# the seam itself
# ---------------------------------------------------------------------------


def test_the_provider_declares_that_it_retrieves_for_itself() -> None:
    # The flag the runner reads to skip selection. If this regressed to False the
    # runner would embed the whole corpus to build a page list that is then ignored.
    assert OrchestratorProvider.owns_retrieval is True


def test_each_intent_names_its_own_model() -> None:
    client = FakeClient()
    provider, _ = _provider(client)

    assert provider.model_for("ask") == provider._config.ask_model
    assert provider.model_for("draft") == provider._config.draft_model
    assert provider.model_for("summary") == provider._config.summary_model_small
    # The three are not the same setting, which is the reason `model_for` exists.
    assert len({provider.model_for(i) for i in ("ask", "draft", "summary")}) >= 2


def test_a_missing_key_is_refused_at_construction() -> None:
    from legal_lens.orchestrator.retrieve import PageStore  # noqa: F401 — import guard

    with pytest.raises(RuntimeError, match="LEGAL_LENS_ANTHROPIC_API_KEY"):
        OrchestratorProvider(
            Settings(environment="test", llm_provider="orchestrator", anthropic_api_key=None),
            FakeRetrieval(FakeRetriever(PAGES, corpus_pages=3)),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_drops_abstentions() -> None:
    """An abstention is not a subject a follow-up can refer back to.

    Leaving it in invites the rewrite step to carry the unanswered question forward
    as though it had been answered — the failure mode the rewrite suite found twice.
    """
    from datetime import UTC, datetime

    from legal_lens.schemas import Message

    def message(role: str, content: str, abstained: bool = False) -> Message:
        return Message(
            id=f"m-{role}-{abs(hash(content)) % 1000}",
            conversation_id="c-1",
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=datetime.now(UTC),
            abstained=abstained,
        )

    turns = history_from_messages(
        [
            message("user", "Does the MSA cap liability?"),
            message("assistant", "Yes, at fees paid."),
            message("user", "What about indemnification?"),
            message("assistant", "The excerpts do not address this.", abstained=True),
            message("assistant", "   "),
        ]
    )

    assert [t.content for t in turns] == [
        "Does the MSA cap liability?",
        "Yes, at fees paid.",
        "What about indemnification?",
    ]
