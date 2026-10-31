"""The chat path: the two-request handshake, the SSE protocol, and persistence.

The protocol assertions here are the ones the frontend silently depends on
(`lib/sse.ts`). Getting the event order wrong produces a chat that hangs or renders
dead citation chips — neither of which raises anything on the server.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from helpers import await_status, ready_document
from legal_lens.main import create_app


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    """Parse frames the way `lib/sse.ts` does: split on a blank line, one JSON line."""
    events: list[tuple[str, dict]] = []
    for frame in raw.split("\n\n"):
        lines = [line for line in frame.strip().split("\n") if line]
        if len(lines) < 2:
            continue
        event = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
        data = next((line[5:].strip() for line in lines if line.startswith("data:")), None)
        if event and data:
            events.append((event, json.loads(data)))
    return events


#: `title` is required, so there is no empty create body.
NEW = {"title": "New conversation"}


async def _conversation(client: AsyncClient) -> str:
    return (await client.post("/conversations", json=NEW)).json()["id"]


async def _send(client: AsyncClient, conversation_id: str, content: str) -> dict:
    response = await client.post(
        "/chat/message", json={"conversationId": conversation_id, "content": content}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _stream(client: AsyncClient, message_id: str) -> list[tuple[str, dict]]:
    response = await client.get(f"/chat/stream?messageId={message_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


# ---------------------------------------------------------------------------
# The handshake
# ---------------------------------------------------------------------------


async def test_posting_a_message_persists_the_user_turn_and_returns_a_stream_id(
    client: AsyncClient,
) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)

    sent = await _send(client, conversation_id, "What are the indemnification obligations?")

    assert sent["intent"] == "ask"
    assert sent["userMessage"]["role"] == "user"
    assert sent["assistantMessageId"].startswith("a-")

    # Only the user turn exists so far. The assistant turn is pending, not persisted —
    # persisting it here would leave an empty message if the stream were never opened.
    messages = (await client.get(f"/conversations/{conversation_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user"]


async def test_the_first_turn_titles_the_conversation(client: AsyncClient) -> None:
    conversation_id = await _conversation(client)
    assert (await client.get("/conversations")).json()[0]["title"] == "New conversation"

    await _send(client, conversation_id, "Early termination rights under the MSA")

    conversations = (await client.get("/conversations")).json()
    renamed = next(c for c in conversations if c["id"] == conversation_id)
    assert renamed["title"] == "Early termination rights under the MSA"
    assert renamed["lastIntent"] == "ask"


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("What are the indemnification obligations?", "ask"),
        ("Summarise the key terms", "summary"),
        ("Summarize this agreement", "summary"),
        ("Draft a memo on indemnification", "draft"),
        ("Prepare a memorandum about venue", "draft"),
    ],
)
async def test_intent_routing_mirrors_the_mock(
    client: AsyncClient, question: str, intent: str
) -> None:
    conversation_id = await _conversation(client)
    assert (await _send(client, conversation_id, question))["intent"] == intent


async def test_messaging_an_unknown_conversation_is_404(client: AsyncClient) -> None:
    response = await client.post(
        "/chat/message", json={"conversationId": "c-nope", "content": "hello"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"


async def test_scoping_to_a_document_that_is_not_ready_is_refused(client: AsyncClient) -> None:
    broken = {"file": ("scan.pdf", b"%PDF-1.7\nbroken", "application/pdf")}
    scan = (await client.post("/documents", files=broken)).json()
    await await_status(client, scan["id"], "failed")

    conversation_id = await _conversation(client)
    response = await client.post(
        "/chat/message",
        json={
            "conversationId": conversation_id,
            "content": "What does it say?",
            "scopedDocumentIds": [scan["id"]],
        },
    )
    # A 409 naming the document, never a silently dropped source: an answer built
    # from two of the three documents the user picked is wrong invisibly.
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "document_not_ready"
    assert body["documentId"] == scan["id"]


# ---------------------------------------------------------------------------
# The SSE protocol
# ---------------------------------------------------------------------------


async def test_meta_precedes_every_token_and_done_arrives_exactly_once(
    client: AsyncClient,
) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")

    events = await _stream(client, sent["assistantMessageId"])
    names = [name for name, _ in events]

    assert names[0] == "meta", "the renderer resolves citations against meta"
    assert names[-1] == "done", "the UI stops spinning on done"
    # Exactly one: the runner and the route's `finally` both used to emit it.
    assert names.count("done") == 1
    assert names.index("meta") < names.index("token")

    first_meta = events[0][1]
    assert first_meta["id"] == sent["assistantMessageId"]
    assert first_meta["intent"] == "ask"
    assert first_meta["citations"] == []


async def test_a_citation_is_sent_before_the_marker_that_references_it(
    client: AsyncClient,
) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")

    events = await _stream(client, sent["assistantMessageId"])

    known: set[int] = set()
    for name, payload in events:
        if name == "meta":
            known = {c["n"] for c in payload.get("citations", [])}
        elif name == "token":
            for n in _markers(payload["text"]):
                # A marker whose citation the client has not seen renders as a dead
                # chip, which is the failure this ordering exists to prevent.
                assert n in known, f"{{{{cite:{n}}}}} arrived before its citation"


def _markers(text: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"\{\{cite:(\d+)\}\}", text)]


async def test_cited_text_appears_verbatim_on_the_page_it_names(client: AsyncClient) -> None:
    """The product's central claim, end to end.

    The viewer highlights by finding `citedText` inside the page it names. If this
    fails, a chip opens the right page and highlights nothing.
    """
    document_id = await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")

    events = await _stream(client, sent["assistantMessageId"])
    citations = [c for name, p in events if name == "meta" for c in p.get("citations", [])]
    assert citations, "a grounded answer must cite something"

    for citation in citations:
        page = (
            await client.get(f"/documents/{citation['documentId']}/page/{citation['page']}")
        ).json()
        assert citation["citedText"] in page["text"], (
            f"citation {citation['n']} quotes text absent from "
            f"{citation['documentId']} page {citation['page']}"
        )
        assert citation["documentId"] == document_id


async def test_structured_intents_send_no_tokens(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "Summarise the key terms")

    events = await _stream(client, sent["assistantMessageId"])
    # A half-rendered summary table reads as broken, so the payload resolves as a
    # unit on `meta` rather than streaming word by word.
    assert not [name for name, _ in events if name == "token"]

    summary = [p["summary"] for name, p in events if name == "meta" and "summary" in p][-1]
    assert len(summary) == 9
    assert [f["key"] for f in summary][:3] == ["parties", "term", "obligations"]


async def test_absent_summary_fields_are_reported_not_omitted(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "Summarise the key terms")

    events = await _stream(client, sent["assistantMessageId"])
    summary = [p["summary"] for name, p in events if name == "meta" and "summary" in p][-1]

    # FR-13: every field is present and an absence is an explicit null. A reader
    # cannot tell a missing clause from a missing answer otherwise.
    assert len(summary) == 9
    assert any(f["value"] is None for f in summary)
    for field in summary:
        assert set(field) >= {"key", "label", "value", "cites"}


async def test_a_draft_marks_facts_the_documents_do_not_supply(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "Draft a memo on indemnification")

    events = await _stream(client, sent["assistantMessageId"])
    draft = [p["draft"] for name, p in events if name == "meta" and "draft" in p][-1]

    # FR-16/FR-17. The marker is styled on render and must survive verbatim.
    assert "[ATTORNEY INPUT REQUIRED]" in draft["body"]
    assert draft["title"]


async def test_abstention_is_an_outcome_not_an_error(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(
        client, conversation_id, "What is the employee stock option vesting schedule?"
    )

    events = await _stream(client, sent["assistantMessageId"])
    final_meta = [p for name, p in events if name == "meta"][-1]

    # FR-9: a correct answer, delivered over a 200, with no substitute invented.
    assert final_meta["abstained"] is True
    assert [name for name, _ in events if name == "done"]

    messages = (await client.get(f"/conversations/{conversation_id}/messages")).json()
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["abstained"] is True
    assert not (assistant.get("citations") or [])


# ---------------------------------------------------------------------------
# Pending turns, persistence and stopping
# ---------------------------------------------------------------------------


async def test_the_turn_and_its_citations_persist_after_the_stream(
    client: AsyncClient,
) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")
    await _stream(client, sent["assistantMessageId"])

    messages = (await client.get(f"/conversations/{conversation_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]

    assistant = messages[1]
    assert assistant["id"] == sent["assistantMessageId"]
    assert assistant["intent"] == "ask"
    assert assistant["grounded"] is True
    assert assistant["citations"], "citations must be written with the message"

    # The dedicated endpoint agrees with the message, because both read one table.
    fetched = (await client.get(f"/chat/{assistant['id']}/citations")).json()
    assert len(fetched["citations"]) == len(assistant["citations"])


async def test_a_pending_turn_can_only_be_streamed_once(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")

    first = await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")
    assert first.status_code == 200
    # A second reader would race the first into a duplicate persisted message.
    second = await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")
    assert second.status_code == 404


async def test_an_unknown_stream_id_is_404(client: AsyncClient) -> None:
    assert (await client.get("/chat/stream?messageId=a-nope")).status_code == 404


async def test_stop_takes_an_empty_body(client: AsyncClient) -> None:
    # The frontend's stop button posts no body but does send a JSON content type. A
    # required Pydantic body would make that a 422.
    assert (await client.post("/chat/stop")).status_code == 204
    assert (await client.post("/chat/stop", json={})).status_code == 204
    assert (await client.post("/chat/stop", json={"messageId": "a-x"})).status_code == 204


async def test_citations_for_an_unknown_message_is_404(client: AsyncClient) -> None:
    assert (await client.get("/chat/a-nope/citations")).status_code == 404


async def test_a_message_with_no_citations_is_not_a_404(client: AsyncClient) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What is the stock option vesting schedule?")
    await _stream(client, sent["assistantMessageId"])

    response = await client.get(f"/chat/{sent['assistantMessageId']}/citations")
    # An abstention has no citations, which is different from having no message.
    assert response.status_code == 200
    assert response.json()["citations"] == []


# ---------------------------------------------------------------------------
# Audit (FR-20)
# ---------------------------------------------------------------------------


async def test_every_generation_writes_an_audit_row(client: AsyncClient, settings) -> None:
    await ready_document(client)
    conversation_id = await _conversation(client)
    sent = await _send(client, conversation_id, "What are the indemnification obligations?")
    await _stream(client, sent["assistantMessageId"])

    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as probe,
        app.router.lifespan_context(app),
    ):
        rows = await app.state.services.db.fetch_all(
            "SELECT feature, retrieval_path, outcome, grounded, model_id FROM audit_log"
        )
        assert probe is not None

    assert len(rows) == 1
    row = rows[0]
    assert row["feature"] == "ask"
    # Below the token budget, so the whole corpus went into context (BRD §4.2).
    assert row["retrieval_path"] == "whole_document"
    assert row["outcome"] == "ok"
    assert row["grounded"] == 1
    assert row["model_id"]
