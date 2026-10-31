"""The Phase 2 regression test, written in Phase 1.

Phase 1 has no login, so every request runs as `PHASE1_OWNER_ID` and these assertions
look like tests of a feature nobody can reach. That is the point: the day
authentication lands, `owner_id` starts varying, and a repository method that ignored
it becomes a cross-tenant data leak. Proving the filter works while there is only one
owner is what keeps that from being a discovery in production (BRD §6.1).
"""

from __future__ import annotations

import pytest

from legal_lens.config import PHASE1_OWNER_ID
from legal_lens.services import Services

OTHER_OWNER = "owner-someone-else"


async def test_conversations_are_invisible_to_another_owner(services: Services) -> None:
    created = await services.conversations.create(PHASE1_OWNER_ID, "m-001", "Mine")

    assert await services.conversations.get(OTHER_OWNER, created.id) is None
    assert await services.conversations.list(OTHER_OWNER) == []
    # And the owner who created it still sees it, so the filter is scoping rather than
    # simply breaking the query.
    assert [c.id for c in await services.conversations.list(PHASE1_OWNER_ID)] == [created.id]


async def test_another_owner_cannot_rename_or_delete(services: Services) -> None:
    created = await services.conversations.create(PHASE1_OWNER_ID, "m-001", "Mine")

    assert await services.conversations.rename(OTHER_OWNER, created.id, "Hijacked") is None
    assert await services.conversations.delete(OTHER_OWNER, created.id) is False

    survived = await services.conversations.get(PHASE1_OWNER_ID, created.id)
    assert survived is not None and survived.title == "Mine"


async def test_messages_are_invisible_to_another_owner(services: Services) -> None:
    conversation = await services.conversations.create(PHASE1_OWNER_ID, "m-001")
    await services.messages.append_user_turn(
        PHASE1_OWNER_ID, conversation.id, "u-1", "What is the term?", None
    )

    assert await services.messages.list_for_conversation(OTHER_OWNER, conversation.id) == []
    assert len(await services.messages.list_for_conversation(PHASE1_OWNER_ID, conversation.id)) == 1


async def test_matters_are_scoped(services: Services) -> None:
    assert await services.matters.list(OTHER_OWNER) == []
    assert await services.matters.exists(OTHER_OWNER, "m-001") is False
    assert await services.matters.exists(PHASE1_OWNER_ID, "m-001") is True


@pytest.mark.parametrize("owner", [PHASE1_OWNER_ID, OTHER_OWNER])
async def test_seeding_is_idempotent_per_owner(services: Services, owner: str) -> None:
    first = await services.matters.ensure_default(owner)
    second = await services.matters.ensure_default(owner)
    assert first == second
    assert len(await services.matters.list(owner)) == 1
