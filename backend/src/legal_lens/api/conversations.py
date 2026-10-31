from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..db.repositories.matters import DEFAULT_MATTER_ID
from ..deps import OwnerDep, ServicesDep
from ..errors import not_found
from ..schemas import (
    Conversation,
    CreateConversationRequest,
    Message,
    RenameConversationRequest,
)

router = APIRouter(tags=["conversations"])


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(services: ServicesDep, owner_id: OwnerDep) -> list[Conversation]:
    return await services.conversations.list(owner_id)


@router.post("/conversations", response_model=Conversation)
async def create_conversation(
    body: CreateConversationRequest,
    services: ServicesDep,
    owner_id: OwnerDep,
) -> Conversation:
    # An omitted matter means the default one (§5.1). The frontend also falls back to
    # the `m-001` literal, so both paths have to agree on which matter that is.
    matter_id = body.matter_id or DEFAULT_MATTER_ID
    if not await services.matters.exists(owner_id, matter_id):
        raise not_found("matter", matter_id)
    return await services.conversations.create(owner_id, matter_id, body.title)


@router.get("/conversations/{conversation_id}/messages", response_model=list[Message])
async def list_messages(
    conversation_id: str,
    services: ServicesDep,
    owner_id: OwnerDep,
) -> list[Message]:
    # A missing conversation is a 404; an existing one with no turns is an empty list.
    # Collapsing the two would make a new conversation look broken.
    if await services.conversations.get(owner_id, conversation_id) is None:
        raise not_found("conversation", conversation_id)
    return await services.messages.list_for_conversation(owner_id, conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=Conversation)
async def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    services: ServicesDep,
    owner_id: OwnerDep,
) -> Conversation:
    conversation = await services.conversations.rename(owner_id, conversation_id, body.title)
    if conversation is None:
        raise not_found("conversation", conversation_id)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    services: ServicesDep,
    owner_id: OwnerDep,
) -> Response:
    if not await services.conversations.delete(owner_id, conversation_id):
        raise not_found("conversation", conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
