"""The pending-turn registry.

`POST /chat/message` persists the user turn and registers a pending assistant turn,
returning its id; generation happens on the `GET /chat/stream` that follows. The turn
lives here in between — which is why the service runs single-worker: a second process
would not have the entry, and the stream would 404 at random.

Entries expire after 120 s. Without that, a client that posts and never opens the
stream leaks an entry per message for the life of the process.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..schemas import Intent

log = logging.getLogger(__name__)

TTL_SECONDS = 120.0


@dataclass
class PendingTurn:
    message_id: str
    conversation_id: str
    owner_id: str
    question: str
    intent: Intent
    scoped_document_ids: list[str] | None
    created_at_monotonic: float
    #: Set when the stream starts, so `POST /chat/stop` can cancel it.
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    #: True once a stream has begun consuming this turn, so a second reader is
    #: refused rather than racing the first into a duplicate persisted message.
    claimed: bool = False


class PendingRegistry:
    def __init__(self, ttl_seconds: float = TTL_SECONDS) -> None:
        self._turns: dict[str, PendingTurn] = {}
        self._ttl = ttl_seconds

    def register(
        self,
        *,
        message_id: str,
        conversation_id: str,
        owner_id: str,
        question: str,
        intent: Intent,
        scoped_document_ids: list[str] | None,
    ) -> PendingTurn:
        self._expire()
        turn = PendingTurn(
            message_id=message_id,
            conversation_id=conversation_id,
            owner_id=owner_id,
            question=question,
            intent=intent,
            scoped_document_ids=scoped_document_ids,
            created_at_monotonic=asyncio.get_running_loop().time(),
        )
        self._turns[message_id] = turn
        return turn

    def claim(self, message_id: str, owner_id: str) -> PendingTurn | None:
        """Hand the turn to a stream, once.

        Returns None for an unknown, expired, foreign or already-claimed id — all of
        which the route reports as 404, because from the client's point of view there
        is no turn to stream.
        """
        self._expire()
        turn = self._turns.get(message_id)
        if turn is None or turn.owner_id != owner_id or turn.claimed:
            return None
        turn.claimed = True
        return turn

    def release(self, message_id: str) -> None:
        self._turns.pop(message_id, None)

    def cancel(self, owner_id: str, message_id: str | None = None) -> int:
        """Signal cancellation. With no id, cancels everything this owner has in flight.

        Phase 1 is single-user, and the frontend's stop button posts an empty body, so
        the no-id form is the one that actually runs.
        """
        cancelled = 0
        for turn in list(self._turns.values()):
            if turn.owner_id != owner_id:
                continue
            if message_id is not None and turn.message_id != message_id:
                continue
            turn.cancel.set()
            cancelled += 1
        return cancelled

    def _expire(self) -> None:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return
        stale = [
            message_id
            for message_id, turn in self._turns.items()
            # A claimed turn is mid-generation: a long answer must not have its entry
            # pulled out from under it at the 120-second mark.
            if not turn.claimed and now - turn.created_at_monotonic > self._ttl
        ]
        for message_id in stale:
            del self._turns[message_id]
        if stale:
            log.info("expired pending turns", extra={"count": len(stale)})

    def __len__(self) -> int:
        return len(self._turns)
