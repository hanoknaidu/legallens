from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse

from ..chat.runner import TurnRunner
from ..deps import OwnerDep, ServicesDep
from ..errors import ApiProblem, not_found
from ..schemas import (
    CitationsResponse,
    SendMessageRequest,
    SendMessageResponse,
    StopRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat/message", response_model=SendMessageResponse)
async def send_message(
    body: SendMessageRequest, services: ServicesDep, owner_id: OwnerDep
) -> SendMessageResponse:
    """Persist the user turn, classify intent, register a pending assistant turn.

    Nothing of the assistant turn is persisted here and no model is called — the id
    comes back so the client can open the stream, and generation happens there.
    """
    conversation = await services.conversations.get(owner_id, body.conversation_id)
    if conversation is None:
        raise not_found("conversation", body.conversation_id)

    # A requested document that is not ready is a 409 naming it, never a silently
    # dropped source: an answer assembled from two of the three documents the user
    # chose is wrong in a way they cannot see.
    if body.scoped_document_ids:
        for document_id in body.scoped_document_ids:
            document = await services.documents.get(owner_id, document_id)
            if document is None:
                raise not_found("document", document_id)
            if document.status != "ready":
                raise ApiProblem(
                    409,
                    "Document is not ready",
                    document.failure_reason or f"{document.title} is still {document.status}.",
                    "document_not_ready",
                    documentId=document_id,
                )

    existing = await services.messages.list_for_conversation(owner_id, body.conversation_id)
    user_message = await services.messages.append_user_turn(
        owner_id=owner_id,
        conversation_id=body.conversation_id,
        message_id=f"u-{uuid.uuid4().hex[:10]}",
        content=body.content,
        scoped_document_ids=body.scoped_document_ids,
    )

    intent = await services.route_intent(owner_id, body.content)
    # The first user turn names an untitled conversation; a title the user chose is
    # left alone.
    await services.conversations.touch(
        owner_id,
        body.conversation_id,
        intent,
        first_turn=body.content if not existing else None,
    )

    assistant_message_id = f"a-{uuid.uuid4().hex[:10]}"
    services.pending.register(
        message_id=assistant_message_id,
        conversation_id=body.conversation_id,
        owner_id=owner_id,
        question=body.content,
        intent=intent,
        scoped_document_ids=body.scoped_document_ids,
    )

    return SendMessageResponse(
        user_message=user_message,
        assistant_message_id=assistant_message_id,
        intent=intent,
    )


def _frame(event: str, payload: dict) -> bytes:
    """One SSE frame.

    `data` must be a single line — the client splits frames on a blank line and reads
    one `data:` line per frame — so the JSON is written without newlines.
    """
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


@router.get("/chat/stream")
async def stream_answer(
    messageId: str,  # noqa: N803 — the query parameter the frontend sends
    services: ServicesDep,
    owner_id: OwnerDep,
) -> StreamingResponse:
    """Generate the assistant turn, streaming it as it is produced.

    An unknown, expired, foreign or already-claimed id is a 404: from the client's
    point of view there is no turn here to stream.
    """
    turn = services.pending.claim(messageId, owner_id)
    if turn is None:
        raise not_found("pending turn", messageId)

    runner = TurnRunner(services)
    provider = services.generation_provider

    async def body() -> AsyncIterator[bytes]:
        try:
            async for event, payload in runner.run(turn, provider):
                yield _frame(event, payload)
        except Exception:
            # The response has already begun, so a status code is no longer available.
            log.exception("stream aborted", extra={"message_id": turn.message_id})
            yield _frame("error", {"message": "The stream ended unexpectedly."})
        finally:
            # Without this the client's spinner never stops. It fires even on an
            # abort, and even if the browser has already gone away.
            yield _frame("done", {"id": turn.message_id})
            services.pending.release(turn.message_id)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tells nginx and friends not to buffer — a proxy that waits for the
            # response to finish turns a live stream into one late blob.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_generation(
    services: ServicesDep,
    owner_id: OwnerDep,
    # Optional body: the frontend's stop button aborts the stream and posts nothing.
    # A required model would make that request a 422.
    body: StopRequest | None = None,
) -> Response:
    cancelled = services.pending.cancel(owner_id, body.message_id if body else None)
    log.info("stop requested", extra={"cancelled": cancelled})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/chat/{message_id}/citations", response_model=CitationsResponse)
async def message_citations(
    message_id: str, services: ServicesDep, owner_id: OwnerDep
) -> CitationsResponse:
    citations = await services.messages.citations_for(owner_id, message_id)
    # `None` means no such message; an empty list means a message with no citations,
    # which is a different and legitimate thing.
    if citations is None:
        raise not_found("message", message_id)
    return CitationsResponse(message_id=message_id, citations=citations)
