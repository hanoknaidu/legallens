"""A self-retrieving provider through the real request path.

`test_orchestrator_provider.py` tests the provider in isolation. This exercises what
the runner does differently for one: skipping selection, resolving an owner-scoped
document list, loading the conversation so far, and building the audit row from what
the provider says it read rather than from a selection nothing used.

The provider here is a stub rather than the orchestrator itself, because what is being
tested is the runner's half of the contract. The stub records what it was handed, so
the test can assert on the *request* as well as the response — which is where this
integration can go quietly wrong: a provider that owns retrieval and is handed an
empty scope abstains on every question, and nothing about that looks like an error.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from helpers import NEW_CONVERSATION, ready_document
from legal_lens.ai.types import (
    CitationFound,
    Finished,
    GenerationEvent,
    GenerationRequest,
    RetrievalReport,
    RewriteRecord,
    TextDelta,
    Usage,
)
from legal_lens.config import Settings
from legal_lens.main import create_app
from legal_lens.schemas import Citation


class ScopeRecordingProvider:
    """Owns its retrieval, and remembers every request it was given."""

    name = "orchestrator"
    owns_retrieval = True

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def model_for(self, intent: str) -> str:
        return {"ask": "claude-sonnet-5-5", "draft": "claude-opus-5-5"}.get(
            intent, "claude-sonnet-5-5"
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        self.requests.append(request)
        document_id = request.scoped_document_ids[0]

        yield TextDelta(text="Venue lies in New Castle County.")
        yield CitationFound(
            citation=Citation(
                n=1,
                document_id=document_id,
                document_title="msa.pdf",
                page=3,
                cited_text="Section 9.1 Governing Law.",
            )
        )
        yield Finished(
            usage=Usage(
                model_id="claude-sonnet-5-5",
                input_tokens=900,
                output_tokens=40,
                purpose="generation:ask",
            ),
            calls=[
                Usage(
                    model_id="claude-haiku-4-5",
                    input_tokens=100,
                    output_tokens=12,
                    purpose="rewrite",
                ),
                Usage(
                    model_id="claude-sonnet-5-5",
                    input_tokens=900,
                    output_tokens=40,
                    purpose="generation:ask",
                ),
            ],
            retrieval=RetrievalReport(
                path="rag",
                document_ids=[document_id],
                pages=4,
                coverage=True,
                rewrite=RewriteRecord(
                    original="Where does venue lie?",
                    searched_for="Where does venue lie under the MSA?",
                    rewritten=True,
                    source="model",
                    reason="Resolved 'venue' against the MSA discussed above.",
                ),
            ),
        )


@pytest_asyncio.fixture
async def orchestrated(tmp_path) -> AsyncIterator[tuple[AsyncClient, ScopeRecordingProvider, Any]]:
    """The app with its generation provider swapped for the recording stub.

    The services container comes out too, because the per-call cost rows this path
    writes have no read endpoint — `/audit` exposes the turn, not its calls.
    """
    settings = Settings(environment="test", data_dir=tmp_path, llm_provider="echo")
    app = create_app(settings)
    provider = ScopeRecordingProvider()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as client:
        async with app.router.lifespan_context(app):
            app.state.services.generation_provider = provider
            yield client, provider, app.state.services


async def _ask(client: AsyncClient, conversation_id: str, question: str) -> str:
    sent = (
        await client.post(
            "/chat/message", json={"conversationId": conversation_id, "content": question}
        )
    ).json()
    await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")
    return sent["assistantMessageId"]


# ---------------------------------------------------------------------------


async def test_the_provider_is_handed_a_scope_and_no_pages(orchestrated) -> None:
    client, provider, _ = orchestrated
    document_id = await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Where does venue lie?")

    assert len(provider.requests) == 1
    request = provider.requests[0]
    # Selection was skipped: no pages were loaded or embedded for it to ignore.
    assert request.pages == []
    # And the scope is the owner's ready documents.
    assert request.scoped_document_ids == [document_id]


async def test_the_audit_row_describes_what_the_provider_read(orchestrated) -> None:
    client, provider, _ = orchestrated
    document_id = await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Where does venue lie?")

    entries = (await client.get("/audit")).json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["retrievalPath"] == "rag"
    assert entry["documentIds"] == [document_id]
    # The model that wrote the answer, not the one that tidied the question.
    assert entry["modelId"] == "claude-sonnet-5-5"
    # Totalled across both calls, so the turn's cost is the turn's cost.
    assert entry["inputTokens"] == 1000
    assert entry["outputTokens"] == 52


async def test_the_audit_row_records_what_was_searched_for(orchestrated) -> None:
    """The rewrite verdict survives the turn.

    This is the step whose failures are invisible in the answer — a wrong rewrite
    retrieves the wrong passages and the prose still reads as confident — so the
    record of what retrieval actually looked for has to outlive the request.
    """
    client, _, _ = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Where does venue lie?")

    entry = (await client.get("/audit")).json()["entries"][0]
    assert entry["rewrite"] == {
        "original": "Where does venue lie?",
        "searchedFor": "Where does venue lie under the MSA?",
        "rewritten": True,
        "source": "model",
        "reason": "Resolved 'venue' against the MSA discussed above.",
    }
    # The question column still holds what the attorney typed, not the rewrite.
    assert entry["question"] == "Where does venue lie?"


async def test_the_audit_row_records_whether_an_absence_was_provable(orchestrated) -> None:
    """`coverage` is what licensed or forbade a claim of silence.

    An abstention under partial coverage and a finding of silence under full coverage
    are different outcomes, and without this column the row cannot tell them apart.
    """
    client, _, _ = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Where does venue lie?")

    entry = (await client.get("/audit")).json()["entries"][0]
    assert entry["coverage"] is True


async def test_a_provider_that_reports_no_retrieval_leaves_the_columns_unknown(
    orchestrated,
) -> None:
    """`None`, not `False` — the difference matters.

    `echo` and `anthropic` do not retrieve for themselves and run no rewrite, so
    their rows record neither. Defaulting those to `False`/`{}` would assert that
    coverage was partial and no rewrite happened, which is a claim about a turn
    nobody measured.
    """
    client, provider, _ = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    class NoReport(ScopeRecordingProvider):
        async def stream(self, request):
            self.requests.append(request)
            yield TextDelta(text="Something.")
            yield Finished(usage=Usage(model_id="echo-stub", purpose="generation:ask"))

    provider.__class__ = NoReport  # the same instance, a different stream

    await _ask(client, conversation["id"], "Where does venue lie?")

    entry = (await client.get("/audit")).json()["entries"][0]
    assert entry["coverage"] is None
    assert entry["rewrite"] is None


async def test_every_model_call_gets_its_own_cost_row(orchestrated) -> None:
    client, _, services = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Where does venue lie?")

    rows = await services.db.fetch_all(
        "SELECT purpose, model_id, input_tokens FROM usage_costs ORDER BY purpose"
    )

    # Two calls, two rows, each naming its own model — which is the whole reason
    # `Usage.purpose` exists: "what is the rewrite step costing us?" has to be
    # answerable from this table without new instrumentation.
    assert [r["purpose"] for r in rows] == ["generation:ask", "rewrite"]
    assert [r["model_id"] for r in rows] == ["claude-sonnet-5-5", "claude-haiku-4-5"]
    assert [r["input_tokens"] for r in rows] == [900, 100]


async def test_the_turn_still_persists_its_citations(orchestrated) -> None:
    client, _, services = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    message_id = await _ask(client, conversation["id"], "Where does venue lie?")

    body = (await client.get(f"/chat/{message_id}/citations")).json()
    assert [c["page"] for c in body["citations"]] == [3]
    assert body["citations"][0]["citedText"] == "Section 9.1 Governing Law."


async def test_the_conversation_so_far_reaches_the_provider(orchestrated) -> None:
    """History is what the rewrite step resolves a follow-up against.

    The question being answered must not be in it: handing the rewrite step the
    question it was asked to resolve invites it to resolve it against itself.
    """
    client, provider, _ = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    await _ask(client, conversation["id"], "Does the MSA cap liability?")
    await _ask(client, conversation["id"], "What about indemnification?")

    first, second = provider.requests
    assert first.history == []

    contents = [t.content for t in second.history]
    assert "Does the MSA cap liability?" in contents
    # The current question is excluded.
    assert "What about indemnification?" not in contents
    assert second.history[-1].role == "assistant"


async def test_a_scoped_question_narrows_what_the_provider_may_read(orchestrated) -> None:
    client, provider, _ = orchestrated
    first = await ready_document(client, "msa.pdf")
    second = await ready_document(client, "nda.pdf", ["MUTUAL NDA. Three-year term."])
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    sent = (
        await client.post(
            "/chat/message",
            json={
                "conversationId": conversation["id"],
                "content": "Where does venue lie?",
                "scopedDocumentIds": [second],
            },
        )
    ).json()
    await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")

    scope = provider.requests[0].scoped_document_ids
    assert scope == [second]
    assert first not in scope


async def test_the_stream_still_speaks_the_protocol_the_frontend_reads(orchestrated) -> None:
    """`meta` before any token, and again after each citation (`lib/sse.ts`)."""
    client, _, services = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()
    sent = (
        await client.post(
            "/chat/message",
            json={"conversationId": conversation["id"], "content": "Where does venue lie?"},
        )
    ).json()

    raw = (await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")).text
    names = [
        line.removeprefix("event: ") for line in raw.splitlines() if line.startswith("event: ")
    ]

    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "token" in names
    # The citation arrived, and a `meta` followed it carrying the chip.
    metas = [
        json.loads(line.removeprefix("data: "))
        for line in raw.splitlines()
        if line.startswith("data: ") and '"citations"' in line
    ]
    assert metas[-1]["citations"][0]["page"] == 3


class SilentProvider:
    """Collects a citation and then ends without ever producing an answer.

    The shape a cancelled generation leaves behind: the workflow was interrupted
    between resolving its sources and yielding its result, so the turn has sources
    and nothing to show.
    """

    name = "orchestrator"
    owns_retrieval = True

    def model_for(self, intent: str) -> str:
        return "claude-sonnet-5-5"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        yield CitationFound(
            citation=Citation(
                n=1,
                document_id=request.scoped_document_ids[0],
                document_title="msa.pdf",
                page=1,
                cited_text="Section 1.1 Definitions.",
            )
        )
        # No TextDelta, no Structured, no Abstained, no Finished.


async def test_a_turn_that_produced_nothing_is_recorded_as_empty(orchestrated) -> None:
    # Before this was recorded, such a turn persisted as a normal `ok` row with empty
    # content and the transcript rendered it as a blank bubble under an intent badge:
    # no answer, no error, no way to tell which had happened. The outcome is what
    # makes "how often does this fire?" answerable from the table.
    client, _, services = orchestrated
    services.generation_provider = SilentProvider()
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    message_id = await _ask(client, conversation["id"], "Where does venue lie?")

    stored = (await client.get(f"/conversations/{conversation['id']}/messages")).json()
    assistant = [m for m in stored if m["role"] == "assistant"][-1]
    assert assistant["id"] == message_id
    assert assistant["content"] == ""
    assert assistant["summary"] is None
    assert assistant["draft"] is None
    # A turn with sources and no answer is not grounded: the sources support nothing.
    assert assistant["abstained"] is not True

    audit = (await client.get("/audit")).json()
    row = next(e for e in audit["entries"] if e["messageId"] == message_id)
    assert row["outcome"] == "empty"


async def test_an_answer_that_arrived_is_not_recorded_as_empty(orchestrated) -> None:
    # The guard above must not fire on an ordinary turn.
    client, _, _ = orchestrated
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    message_id = await _ask(client, conversation["id"], "Where does venue lie?")

    audit = (await client.get("/audit")).json()
    row = next(e for e in audit["entries"] if e["messageId"] == message_id)
    assert row["outcome"] == "ok"
