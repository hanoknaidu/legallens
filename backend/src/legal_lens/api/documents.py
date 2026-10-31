from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse

from ..db.repositories.matters import DEFAULT_MATTER_ID
from ..deps import OwnerDep, ServicesDep, SettingsDep
from ..errors import ApiProblem, not_found
from ..ingestion.constants import (
    detect_kind,
    is_accepted_filename,
    media_type_for,
    suffix_of,
    title_from_filename,
)
from ..schemas import DocumentPage, DocumentStatus, LegalDocument

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=list[LegalDocument])
async def list_documents(
    services: ServicesDep,
    owner_id: OwnerDep,
    matterId: str | None = None,  # noqa: N803 — the query parameter the frontend sends
) -> list[LegalDocument]:
    return await services.documents.list(owner_id, matterId)


@router.post("/documents", response_model=LegalDocument, status_code=status.HTTP_201_CREATED)
async def upload_document(
    services: ServicesDep,
    settings: SettingsDep,
    owner_id: OwnerDep,
    file: Annotated[UploadFile, File()],
    # The field names are the multipart keys the frontend sends, so they are camelCase
    # here rather than renamed through an alias nobody would look for.
    matterId: Annotated[str | None, Form()] = None,  # noqa: N803
    title: Annotated[str | None, Form()] = None,
) -> LegalDocument:
    """Accept the bytes, persist the row, and hand parsing to a background task.

    The response is a `queued` document rather than a finished one: parsing a 500-page
    PDF inside the request would hold the upload open with nothing to show for it, and
    the frontend already polls `/status` for exactly this.
    """
    filename = file.filename or "upload"

    # The client gates on both of these before uploading (DocumentsPage.tsx), which is
    # a courtesy to the user rather than a control — so both are checked again here.
    if not is_accepted_filename(filename):
        raise ApiProblem(
            415,
            "File type is not supported",
            "Only PDF, DOCX and TXT files can be indexed.",
            "unsupported_media_type",
        )

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise ApiProblem(
            413,
            "File is too large",
            f"{len(data) / 1024 / 1024:.1f}MB exceeds the {limit_mb}MB limit.",
            "file_too_large",
        )
    if not data:
        raise ApiProblem(422, "File is empty", "The uploaded file contains no data.", "empty_file")

    matter_id = matterId or DEFAULT_MATTER_ID
    if not await services.matters.exists(owner_id, matter_id):
        raise not_found("matter", matter_id)

    if await services.documents.count_for_matter(owner_id, matter_id) >= (
        settings.max_documents_per_matter
    ):
        raise ApiProblem(
            409,
            "Matter is full",
            f"This matter already holds {settings.max_documents_per_matter} documents.",
            "matter_document_limit",
        )

    document_id = f"d-{uuid.uuid4().hex[:10]}"
    object_key = f"{document_id}{suffix_of(filename)}"
    checksum = services.store.put(object_key, data)

    document = await services.documents.create(
        owner_id=owner_id,
        document_id=document_id,
        matter_id=matter_id,
        title=title or title_from_filename(filename),
        filename=filename,
        kind=detect_kind(filename),
        media_type=file.content_type or media_type_for(filename),
        size_bytes=len(data),
        object_key=object_key,
        checksum=checksum,
    )

    # Fire and forget, with a reference held so the task is not garbage collected
    # mid-parse — see `Services.track`.
    services.track(
        asyncio.create_task(services.ingestion.run(owner_id, document_id, object_key, filename))
    )
    return document


@router.get("/documents/{document_id}", response_model=LegalDocument)
async def get_document(
    document_id: str, services: ServicesDep, owner_id: OwnerDep
) -> LegalDocument:
    document = await services.documents.get(owner_id, document_id)
    if document is None:
        raise not_found("document", document_id)
    return document


@router.get("/documents/{document_id}/status", response_model=DocumentStatus)
async def get_document_status(
    document_id: str, services: ServicesDep, owner_id: OwnerDep
) -> DocumentStatus:
    document_status = await services.documents.status(owner_id, document_id)
    if document_status is None:
        raise not_found("document", document_id)
    return document_status


@router.get("/documents/{document_id}/page/{page}", response_model=DocumentPage)
async def get_document_page(
    document_id: str, page: int, services: ServicesDep, owner_id: OwnerDep
) -> DocumentPage:
    document = await services.documents.get(owner_id, document_id)
    if document is None:
        raise not_found("document", document_id)

    # A document that produced no text layer has no page to serve, and rendering an
    # empty page as though it were a blank one is the FR-2 failure this prevents.
    if document.status != "ready":
        raise ApiProblem(
            409,
            "Document is not ready",
            document.failure_reason or f"{document_id} is still {document.status}.",
            "document_not_ready",
        )

    found = await services.documents.page(owner_id, document_id, page)
    if found is None:
        raise ApiProblem(
            416,
            "Page is out of range",
            f"{document_id} has {document.pages} pages; page {page} was requested.",
            "page_out_of_range",
        )
    return found


@router.get("/documents/{document_id}/file")
async def get_document_file(
    document_id: str, services: ServicesDep, owner_id: OwnerDep
) -> FileResponse:
    located = await services.documents.object_key(owner_id, document_id)
    if located is None:
        raise not_found("document", document_id)

    object_key, filename, media_type = located
    path = services.store.path_for(object_key)
    if path is None:
        raise not_found("document file", document_id)

    # `inline` so pdf.js renders it in the viewer rather than the browser downloading
    # it; range support is what lets pdf.js fetch one page at a time.
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Accept-Ranges": "bytes",
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: str, services: ServicesDep, owner_id: OwnerDep) -> Response:
    located = await services.documents.object_key(owner_id, document_id)
    if not await services.documents.delete(owner_id, document_id):
        raise not_found("document", document_id)

    # Pages, chunks and citations went with the row inside SQLite. Everything SQLite
    # cannot reach gets a job row, resolved immediately where possible, so an
    # incomplete purge is visible rather than assumed (NFR-8).
    if located is not None:
        removed = services.store.remove(located[0])
        await services.documents.record_deletion_job(
            owner_id,
            document_id,
            "object_store",
            f"del-{uuid.uuid4().hex[:10]}",
            "done" if removed else "pending",
            None if removed else "object store delete failed; awaiting reconciliation",
        )

    # The vector indexes are the third copy of the text. The orchestrator's Qdrant
    # persists across a restart, so a document left in it answers questions after the
    # user deleted it — the same purge job the object store gets, for the same reason.
    forgotten = services.forget_document(document_id)
    await services.documents.record_deletion_job(
        owner_id,
        document_id,
        "vector_index",
        f"del-{uuid.uuid4().hex[:10]}",
        "done" if forgotten else "pending",
        None if forgotten else "vector index purge failed; awaiting reconciliation",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
