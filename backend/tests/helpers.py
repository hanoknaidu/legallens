"""Shared test helpers.

The polling helper lives here rather than in each test module because ingestion is
asynchronous: every test that needs a `ready` document has to wait the way the
frontend does, and three copies of that loop was three places for the timeout to
drift.
"""

from __future__ import annotations

import asyncio
import os

from httpx import AsyncClient

from fixtures import text_pdf

#: A realistic MSA, used wherever a test needs a document with citable clauses.
MSA_PAGES = [
    "MASTER SERVICES AGREEMENT between Northwind Logistics, Inc. and Arbor Systems LLC.",
    "Section 7.2 Indemnification. The Supplier shall indemnify the Customer against "
    "all third-party claims arising from the Services.",
    "Section 9.1 Governing Law. This Agreement is governed by the laws of Delaware, "
    "and venue lies exclusively in New Castle County.",
    "Section 12.1 Insurance. The Supplier shall maintain commercial general liability "
    "insurance with limits of not less than USD 5,000,000 per occurrence.",
]

#: `title` is required on create, so there is no empty create body.
NEW_CONVERSATION = {"title": "New conversation"}

#: How long a document may take to reach a terminal state before the test calls it
#: a failure. 10s is generous on a developer machine, where the whole suite runs
#: in about 20s, and the point of a tight budget is that a pipeline which leaves a
#: row mid-flight is a real defect rather than a slow test.
#:
#: A shared CI runner is a different machine: on GitHub's two-core runners the
#: py3.12 job reproducibly blew the 10s budget on
#: `test_posting_a_message_persists_the_user_turn_and_returns_a_stream_id` while
#: py3.14 passed, with the document still mid-flight rather than `failed` — the
#: pipeline was working, just not that fast. Rather than slacken the budget for
#: everyone, CI raises it via this variable and keeps the strict default local.
TERMINAL_TIMEOUT_S = float(os.environ.get("LEGAL_LENS_TEST_TERMINAL_TIMEOUT_S", "10.0"))
POLL_INTERVAL_S = 0.05


async def await_status(client: AsyncClient, document_id: str, wanted: str) -> dict:
    """Poll `/status` until the document reaches `wanted`.

    A timeout here is a real failure rather than a slow test: it means the pipeline
    left the row mid-flight, which is the state that makes the UI poll forever.
    """
    try:
        async with asyncio.timeout(TERMINAL_TIMEOUT_S):
            while True:
                body = (await client.get(f"/documents/{document_id}/status")).json()
                if body["status"] == wanted:
                    return body
                if body["status"] in ("ready", "failed"):
                    raise AssertionError(f"wanted {wanted}, got {body}")
                await asyncio.sleep(POLL_INTERVAL_S)
    except TimeoutError:
        raise AssertionError(
            f"{document_id} never reached {wanted!r} within {TERMINAL_TIMEOUT_S}s"
        ) from None


async def ready_document(
    client: AsyncClient, name: str = "msa.pdf", pages: list[str] | None = None
) -> str:
    """Upload a text PDF and return its id once it is indexed."""
    files = {"file": (name, text_pdf(pages or MSA_PAGES), "application/pdf")}
    created = (await client.post("/documents", files=files)).json()
    assert created["status"] == "queued", created
    await await_status(client, created["id"], "ready")
    return created["id"]


async def finished_turn(client: AsyncClient, question: str) -> str:
    """Produce one fully streamed, persisted assistant turn; return its message id."""
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()
    sent = (
        await client.post(
            "/chat/message", json={"conversationId": conversation["id"], "content": question}
        )
    ).json()
    # Draining the stream is what persists the turn.
    await client.get(f"/chat/stream?messageId={sent['assistantMessageId']}")
    return sent["assistantMessageId"]
