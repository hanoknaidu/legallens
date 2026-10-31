from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..deps import OwnerDep, ServicesDep
from ..schemas import AuditEntry, CostByPurpose, Wire

router = APIRouter(tags=["audit"])


class AuditPage(Wire):
    entries: list[AuditEntry]
    total: int
    #: Spend across every model call this owner has made, not only the ones on this
    #: page — it answers "what has this cost?", which is not a question about a page.
    total_cost_usd: float = 0.0


class CostReport(Wire):
    """Spend per pipeline step (NFR-10)."""

    total_cost_usd: float
    by_purpose: list[CostByPurpose]


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    services: ServicesDep,
    owner_id: OwnerDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    feature: str | None = None,
    conversationId: str | None = None,  # noqa: N803 — the query parameter clients send
) -> AuditPage:
    entries, total = await services.audit.list(
        owner_id, limit=limit, offset=offset, feature=feature, conversation_id=conversationId
    )
    return AuditPage(
        entries=entries,
        total=total,
        total_cost_usd=await services.audit.total_cost(owner_id),
    )


@router.get("/audit/costs", response_model=CostReport)
async def cost_report(services: ServicesDep, owner_id: OwnerDep) -> CostReport:
    """What the pipeline costs, broken down by the step that spent it (NFR-10).

    The rows behind this have been written since the first turn and read by nothing,
    which made "is the retrieval or the routing the expensive part?" a question you
    had to open the database to answer.
    """
    return CostReport(
        total_cost_usd=await services.audit.total_cost(owner_id),
        by_purpose=[CostByPurpose(**row) for row in await services.audit.cost_by_purpose(owner_id)],
    )


@router.get("/audit/export")
async def export_audit(
    services: ServicesDep,
    owner_id: OwnerDep,
    format: str = Query("jsonl", pattern="^jsonl$"),
) -> StreamingResponse:
    """Stream the whole audit log as JSONL (NFR-9).

    Streamed in pages rather than assembled in memory: twelve months of retention is
    the requirement, and a year of rows materialised into one list is a request that
    gets slower and heavier every month it is not used.
    """

    async def rows() -> AsyncIterator[bytes]:
        offset = 0
        page_size = 500
        while True:
            entries, _ = await services.audit.list(owner_id, limit=page_size, offset=offset)
            if not entries:
                return
            for entry in entries:
                # Pydantic's own JSON serialiser, not `json.dumps(default=str)`:
                # `str(datetime)` emits "2026-10-10 13:37:43+00:00" — a space instead
                # of the ISO "T" — so the export disagreed with `GET /audit` about the
                # same row, and an archive meant to be machine-readable held a
                # non-ISO timestamp.
                yield entry.model_dump_json(by_alias=True).encode() + b"\n"
            if len(entries) < page_size:
                return
            offset += page_size

    return StreamingResponse(
        rows(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="legal-lens-audit.jsonl"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
