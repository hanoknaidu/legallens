"""Sidebar title behaviour (§5.1).

Nothing calls `touch` until `POST /chat/message` lands in Phase 3, so these are the
only thing holding the behaviour in place meanwhile. They matter because the rule is
mirrored from the mock: the sidebar has to read identically whether the data is mocked
or real, and "replaced by the first user turn" is easy to implement as "replaced by
every user turn", which would silently discard a title the user chose.
"""

from __future__ import annotations

import pathlib

import pytest
from httpx import AsyncClient

from legal_lens.config import PHASE1_OWNER_ID
from legal_lens.db.repositories.conversations import (
    DEFAULT_TITLE,
    TITLE_MAX_CHARS,
    derive_title,
)
from legal_lens.services import Services


def test_short_questions_are_used_verbatim() -> None:
    assert derive_title("What is the term?") == "What is the term?"
    assert derive_title("  padded  ") == "padded"


def test_long_questions_are_truncated_with_an_ellipsis() -> None:
    question = "x" * 200
    title = derive_title(question)

    # 52 characters plus the ellipsis, matching the mock's slice exactly.
    assert title == "x" * TITLE_MAX_CHARS + "…"
    assert len(title) == TITLE_MAX_CHARS + 1


def test_a_question_exactly_at_the_limit_is_not_truncated() -> None:
    question = "y" * TITLE_MAX_CHARS
    assert derive_title(question) == question
    assert "…" not in derive_title(question)


#: `title` is required, so there is no empty create body.
NEW = {"title": "New conversation"}


async def test_first_turn_names_an_untitled_conversation(services: Services) -> None:
    conversation = await services.conversations.create(PHASE1_OWNER_ID, "m-001")
    assert conversation.title == DEFAULT_TITLE

    await services.conversations.touch(
        PHASE1_OWNER_ID, conversation.id, intent="ask", first_turn="Early termination rights"
    )

    renamed = await services.conversations.get(PHASE1_OWNER_ID, conversation.id)
    assert renamed is not None
    assert renamed.title == "Early termination rights"
    assert renamed.last_intent == "ask"


async def test_a_user_chosen_title_is_never_overwritten(services: Services) -> None:
    conversation = await services.conversations.create(PHASE1_OWNER_ID, "m-001", "My own title")

    await services.conversations.touch(
        PHASE1_OWNER_ID, conversation.id, intent="ask", first_turn="Some later question"
    )

    kept = await services.conversations.get(PHASE1_OWNER_ID, conversation.id)
    assert kept is not None and kept.title == "My own title"


async def test_touch_moves_a_conversation_up_the_sidebar(services: Services) -> None:
    older = await services.conversations.create(PHASE1_OWNER_ID, "m-001", "older")
    await services.conversations.create(PHASE1_OWNER_ID, "m-001", "newer")

    assert [c.title for c in await services.conversations.list(PHASE1_OWNER_ID)] == [
        "newer",
        "older",
    ]

    await services.conversations.touch(PHASE1_OWNER_ID, older.id, intent="summary")

    # The ordering is `updated_at DESC`, so a turn in the older thread brings it back
    # to the top — which is what makes the sidebar a recency list rather than a log.
    assert [c.title for c in await services.conversations.list(PHASE1_OWNER_ID)] == [
        "older",
        "newer",
    ]


# ---------------------------------------------------------------------------
# Title validation on the wire — create and rename must agree
# ---------------------------------------------------------------------------


async def test_create_accepts_a_title(client: AsyncClient) -> None:
    created = (await client.post("/conversations", json={"title": "Indemnification caps"})).json()
    assert created["title"] == "Indemnification caps"


async def test_create_and_rename_enforce_the_same_length_limit(client: AsyncClient) -> None:
    """They disagreed before `TITLE_MAX_LENGTH` was shared.

    `POST` accepted a 300-character title that `PATCH` rejected with a 422, so a
    conversation could be created in a state it could not be renamed back into.
    """
    too_long = "x" * 300

    assert (await client.post("/conversations", json={"title": too_long})).status_code == 422

    created = (await client.post("/conversations", json=NEW)).json()
    patched = await client.patch(f"/conversations/{created['id']}", json={"title": too_long})
    assert patched.status_code == 422


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
async def test_a_blank_title_is_refused_on_both_routes(client: AsyncClient, blank: str) -> None:
    # A whitespace title would leave the sidebar row looking empty, and because it is
    # not the placeholder string the first-user-turn rename would never replace it —
    # so the row would stay blank forever. Both routes refuse it.
    assert (await client.post("/conversations", json={"title": blank})).status_code == 422

    created = (await client.post("/conversations", json=NEW)).json()
    renamed = await client.patch(f"/conversations/{created['id']}", json={"title": blank})
    assert renamed.status_code == 422


async def test_an_omitted_title_is_refused(client: AsyncClient) -> None:
    # `title` is required: the client decides the initial name, including the
    # placeholder, so there is one place that decision lives.
    response = await client.post("/conversations", json={})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("title:")


async def test_titles_are_trimmed(client: AsyncClient) -> None:
    created = (await client.post("/conversations", json={"title": "  Early termination  "})).json()
    assert created["title"] == "Early termination"

    renamed = (
        await client.patch(
            f"/conversations/{created['id']}", json={"title": "  Venue and governing law  "}
        )
    ).json()
    assert renamed["title"] == "Venue and governing law"


async def test_a_title_at_the_limit_is_accepted(client: AsyncClient) -> None:
    from legal_lens.schemas import TITLE_MAX_LENGTH

    exact = "y" * TITLE_MAX_LENGTH
    created = await client.post("/conversations", json={"title": exact})
    assert created.status_code == 200
    assert created.json()["title"] == exact


async def test_an_explicit_title_survives_the_first_user_turn(client: AsyncClient) -> None:
    """A title the user chose is never overwritten by the auto-naming rule."""
    created = (await client.post("/conversations", json={"title": "My own title"})).json()
    await client.post(
        "/chat/message",
        json={"conversationId": created["id"], "content": "What are the payment terms?"},
    )

    listed = (await client.get("/conversations")).json()
    assert next(c for c in listed if c["id"] == created["id"])["title"] == "My own title"


def test_the_placeholder_title_matches_the_frontend_constant() -> None:
    """The one string both codebases have to agree on.

    `POST /conversations` now requires a title, so the client sends the placeholder
    for an unnamed conversation. The backend renames a conversation from its first
    user turn **only while the stored title still equals `DEFAULT_TITLE`** — so if
    either side's literal drifts, auto-naming stops working and nothing raises: new
    conversations simply stay called "New conversation" forever.
    """
    import re

    constants = (
        pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "constants.ts"
    )
    if not constants.is_file():  # pragma: no cover - backend checked out alone
        pytest.skip("frontend not present")

    match = re.search(r'DEFAULT_CONVERSATION_TITLE\s*=\s*"([^"]*)"', constants.read_text())
    assert match, "DEFAULT_CONVERSATION_TITLE not found in frontend/src/lib/constants.ts"
    assert match.group(1) == DEFAULT_TITLE, (
        f"frontend sends {match.group(1)!r} but the backend auto-renames only "
        f"{DEFAULT_TITLE!r}; auto-naming would silently stop"
    )
