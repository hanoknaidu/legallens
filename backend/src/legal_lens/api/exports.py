from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from ..deps import OwnerDep, ServicesDep
from ..errors import ApiProblem, not_found
from ..exporting import export_message
from ..exporting.render import NothingToExport
from ..observability import METRICS
from ..schemas import ExportRequest

log = logging.getLogger(__name__)

router = APIRouter(tags=["exports"])


@router.post("/messages/{message_id}/export")
async def export(
    message_id: str,
    body: ExportRequest,
    services: ServicesDep,
    owner_id: OwnerDep,
) -> Response:
    """Render one assistant turn as a file.

    FR-19: this produces bytes and hands them back. Nothing is sent, filed or served.
    """
    message = await services.messages.get(owner_id, message_id)
    if message is None:
        raise not_found("message", message_id)

    citations = await services.messages.citations_for(owner_id, message_id) or []

    try:
        result = export_message(message, citations, body.format)
    except NothingToExport as exc:
        # 409 rather than 422: the request is well formed, the message simply has
        # nothing exportable in it — an abstention, or a turn that was stopped before
        # it produced anything.
        raise ApiProblem(
            409,
            "Nothing to export",
            f"This message has no draft, summary or answer text to export ({exc}).",
            "nothing_to_export",
        ) from exc

    METRICS.incr("export", fmt=body.format)
    log.info(
        "message exported",
        extra={"message_id": message_id, "fmt": body.format, "bytes": len(result.content)},
    )
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            # `attachment` so the browser saves it rather than trying to render it.
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Content-Length": str(len(result.content)),
            # The filename is the only thing the client needs that is not in the body,
            # and `fetch` cannot read Content-Disposition cross-origin without this.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
