from __future__ import annotations

from collections import defaultdict

import aiosqlite

from ...clock import iso
from ...schemas import Citation, DraftPayload, Intent, Message, SummaryField
from ..session import Database, from_json, to_json


def _to_bool(value: int | None) -> bool | None:
    """SQLite has no boolean. `None` must survive as `None`, not collapse to False.

    `grounded` and `abstained` are tri-state on the wire: absent on a user turn,
    meaningful on an assistant turn. Mapping NULL to False would mark every user
    message as ungrounded.
    """
    return None if value is None else bool(value)


def _to_message(row: aiosqlite.Row, citations: list[Citation] | None) -> Message:
    summary = from_json(row["summary_json"])
    draft = from_json(row["draft_json"])
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        created_at=row["created_at"],
        intent=row["intent"],
        citations=citations or None,
        grounded=_to_bool(row["grounded"]),
        abstained=_to_bool(row["abstained"]),
        summary=[SummaryField(**f) for f in summary] if summary else None,
        draft=DraftPayload(**draft) if draft else None,
        scoped_document_ids=from_json(row["scoped_document_ids"]),
    )


_COLUMNS = """id, conversation_id, role, content, intent, grounded, abstained,
              summary_json, draft_json, scoped_document_ids, created_at, sequence"""


class MessageRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_for_conversation(self, owner_id: str, conversation_id: str) -> list[Message]:
        rows = await self._db.fetch_all(
            f"""SELECT {_COLUMNS} FROM messages
                WHERE conversation_id = ? AND owner_id = ? ORDER BY sequence""",
            (conversation_id, owner_id),
        )
        if not rows:
            return []

        # One query for every citation in the conversation rather than one per message:
        # a long thread would otherwise issue dozens of round trips through a lock that
        # serialises them.
        cite_rows = await self._db.fetch_all(
            """SELECT c.message_id, c.n, c.document_id, c.document_title, c.page, c.cited_text
               FROM message_citations c
               JOIN messages m ON m.id = c.message_id
               WHERE m.conversation_id = ? AND m.owner_id = ?
               ORDER BY c.message_id, c.n""",
            (conversation_id, owner_id),
        )
        grouped: dict[str, list[Citation]] = defaultdict(list)
        for c in cite_rows:
            grouped[c["message_id"]].append(
                Citation(
                    n=c["n"],
                    document_id=c["document_id"],
                    document_title=c["document_title"],
                    page=c["page"],
                    cited_text=c["cited_text"],
                )
            )
        return [_to_message(r, grouped.get(r["id"])) for r in rows]

    async def get(self, owner_id: str, message_id: str) -> Message | None:
        """One message, with its citations. Used by export."""
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM messages WHERE id = ? AND owner_id = ?",
            (message_id, owner_id),
        )
        if row is None:
            return None

        cite_rows = await self._db.fetch_all(
            """SELECT n, document_id, document_title, page, cited_text
               FROM message_citations WHERE message_id = ? ORDER BY n""",
            (message_id,),
        )
        citations = [
            Citation(
                n=c["n"],
                document_id=c["document_id"],
                document_title=c["document_title"],
                page=c["page"],
                cited_text=c["cited_text"],
            )
            for c in cite_rows
        ]
        return _to_message(row, citations)

    async def citations_for(self, owner_id: str, message_id: str) -> list[Citation] | None:
        """`None` when the message does not exist — distinct from a message with none."""
        exists = await self._db.fetch_one(
            "SELECT 1 FROM messages WHERE id = ? AND owner_id = ?", (message_id, owner_id)
        )
        if exists is None:
            return None
        rows = await self._db.fetch_all(
            """SELECT n, document_id, document_title, page, cited_text
               FROM message_citations WHERE message_id = ? ORDER BY n""",
            (message_id,),
        )
        return [
            Citation(
                n=r["n"],
                document_id=r["document_id"],
                document_title=r["document_title"],
                page=r["page"],
                cited_text=r["cited_text"],
            )
            for r in rows
        ]

    async def _next_sequence(self, conversation_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        return int(row["next"]) if row else 1

    async def append_user_turn(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        scoped_document_ids: list[str] | None,
    ) -> Message:
        now = iso()
        sequence = await self._next_sequence(conversation_id)
        await self._db.execute(
            """INSERT INTO messages
                   (id, conversation_id, owner_id, role, content, scoped_document_ids,
                    created_at, sequence)
               VALUES (?, ?, ?, 'user', ?, ?, ?, ?)""",
            (
                message_id,
                conversation_id,
                owner_id,
                content,
                to_json(scoped_document_ids) if scoped_document_ids else None,
                now,
                sequence,
            ),
        )
        return Message(
            id=message_id,
            conversation_id=conversation_id,
            role="user",
            content=content,
            created_at=now,
            scoped_document_ids=scoped_document_ids,
        )

    async def persist_assistant_turn(
        self,
        owner_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        intent: Intent,
        citations: list[Citation],
        grounded: bool,
        abstained: bool,
        summary: list[SummaryField] | None = None,
        draft: DraftPayload | None = None,
    ) -> None:
        """Write the turn and its citations in one transaction.

        Not two statements: a message whose citations failed to write renders as an
        uncited factual assertion, which NFR-1 forbids. Either both land or neither does.
        """
        sequence = await self._next_sequence(conversation_id)
        statements: list[tuple[str, tuple]] = [
            (
                """INSERT INTO messages
                       (id, conversation_id, owner_id, role, content, intent, grounded,
                        abstained, summary_json, draft_json, created_at, sequence)
                   VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    conversation_id,
                    owner_id,
                    content,
                    intent,
                    int(grounded),
                    int(abstained),
                    to_json([f.model_dump(by_alias=False) for f in summary]) if summary else None,
                    to_json(draft.model_dump(by_alias=False)) if draft else None,
                    iso(),
                    sequence,
                ),
            )
        ]
        statements += [
            (
                """INSERT INTO message_citations
                       (message_id, n, document_id, document_title, page, cited_text)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message_id, c.n, c.document_id, c.document_title, c.page, c.cited_text),
            )
            for c in citations
        ]
        await self._db.execute_script(statements)
