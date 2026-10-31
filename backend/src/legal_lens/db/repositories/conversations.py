from __future__ import annotations

import uuid

import aiosqlite

from ...clock import iso
from ...schemas import Conversation, Intent
from ..session import Database

# The sidebar's title behaviour, mirrored from the mock so the two are
# indistinguishable: an untitled conversation is named by its first user turn,
# truncated with an ellipsis. See mocks/handlers/chat.ts.
DEFAULT_TITLE = "New conversation"
TITLE_MAX_CHARS = 52


def derive_title(first_user_turn: str) -> str:
    text = first_user_turn.strip()
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return f"{text[:TITLE_MAX_CHARS]}…"


def _to_conversation(row: aiosqlite.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        title=row["title"],
        matter_id=row["matter_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_intent=row["last_intent"],
    )


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, owner_id: str) -> list[Conversation]:
        rows = await self._db.fetch_all(
            """SELECT id, title, matter_id, last_intent, created_at, updated_at
               FROM conversations WHERE owner_id = ?
               -- `rowid` only breaks ties, and it is insertion order rather than
               -- the uuid's arbitrary order: a recency list whose rows swap places
               -- between refetches reads as a bug.
               ORDER BY updated_at DESC, rowid DESC""",
            (owner_id,),
        )
        return [_to_conversation(r) for r in rows]

    async def get(self, owner_id: str, conversation_id: str) -> Conversation | None:
        row = await self._db.fetch_one(
            """SELECT id, title, matter_id, last_intent, created_at, updated_at
               FROM conversations WHERE id = ? AND owner_id = ?""",
            (conversation_id, owner_id),
        )
        return _to_conversation(row) if row else None

    async def create(self, owner_id: str, matter_id: str, title: str | None = None) -> Conversation:
        now = iso()
        conversation_id = f"c-{uuid.uuid4().hex[:10]}"
        await self._db.execute(
            """INSERT INTO conversations
                   (id, owner_id, matter_id, title, last_intent, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (conversation_id, owner_id, matter_id, title or DEFAULT_TITLE, now, now),
        )
        return Conversation(
            id=conversation_id,
            title=title or DEFAULT_TITLE,
            matter_id=matter_id,
            created_at=now,
            updated_at=now,
        )

    async def rename(self, owner_id: str, conversation_id: str, title: str) -> Conversation | None:
        changed = await self._db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
            (title, iso(), conversation_id, owner_id),
        )
        if not changed:
            return None
        return await self.get(owner_id, conversation_id)

    async def touch(
        self,
        owner_id: str,
        conversation_id: str,
        intent: Intent | None = None,
        first_turn: str | None = None,
    ) -> None:
        """Move a conversation to the top of the sidebar after a turn.

        `first_turn` retitles only a conversation still carrying the default name, so a
        title the user chose is never overwritten.
        """
        if first_turn is not None:
            await self._db.execute(
                """UPDATE conversations SET updated_at = ?, last_intent = ?,
                       title = CASE WHEN title = ? THEN ? ELSE title END
                   WHERE id = ? AND owner_id = ?""",
                (
                    iso(),
                    intent,
                    DEFAULT_TITLE,
                    derive_title(first_turn),
                    conversation_id,
                    owner_id,
                ),
            )
            return
        await self._db.execute(
            """UPDATE conversations SET updated_at = ?, last_intent = ?
               WHERE id = ? AND owner_id = ?""",
            (iso(), intent, conversation_id, owner_id),
        )

    async def delete(self, owner_id: str, conversation_id: str) -> bool:
        # Messages and their citations cascade in SQLite (schema.sql).
        changed = await self._db.execute(
            "DELETE FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
        return bool(changed)
