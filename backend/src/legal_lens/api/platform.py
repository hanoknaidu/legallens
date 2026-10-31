"""Health and metrics (§5.7).

`degraded` never means 503. A missing API key degrades generation; it does not take
the service down, and the frontend has to be able to tell the difference — so the
status code stays 200 and `checks` names the capability that is missing.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import Settings
from ..deps import ServicesDep, SettingsDep
from ..observability import METRICS
from ..schemas import HealthResponse

router = APIRouter(tags=["platform"])


def _object_store_state(settings: Settings) -> str:
    """Probe for writability, not existence: a read-only mount passes `exists()`."""
    probe = settings.objects_path / ".health"
    try:
        settings.objects_path.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
        return "ok"
    except OSError as exc:
        return f"unwritable: {exc.strerror or exc}"


def _llm_state(services, settings: Settings) -> str:
    """Degraded, never fatal.

    The provider is needed only by a generation call. Upload, parse, retrieval and
    every read path work without it, so the service stays useful and says so.

    Reports the startup probe rather than whether a key is merely present, because a
    revoked key, an exhausted balance and a key missing its workspace scope all look
    identical from the outside and all three used to read as `ok`.
    """
    return services.llm_probe.as_check(settings)


def _vector_store_state(services, settings: Settings) -> str:
    """Which store is actually answering questions.

    Under `llm_provider="orchestrator"` this one is still populated — so that
    switching providers does not need a re-index — but nothing reads it. Reporting
    "ok (memory)" there would name a store that no question touches.
    """
    if services.orchestrated is not None:
        return "ok (not in use; the orchestrator serves retrieval)"
    if settings.vector_store == "memory":
        return "ok (memory, process-local)"
    return f"ok (qdrant {settings.qdrant_url})"


def _ocr_state(services, settings: Settings) -> str:
    if services.ocr is not None:
        return f"ok ({services.ocr.name})"
    if not settings.ocr_enabled:
        return "ok (disabled: scanned PDFs are rejected per FR-2)"
    # Configured on but not usable. Named precisely, because the symptom otherwise
    # looks identical to OCR being switched off.
    reason = getattr(services, "ocr_unavailable_reason", None)
    return f"enabled but unavailable: {reason or 'engine could not be built'}"


@router.get("/health", response_model=HealthResponse)
async def health(services: ServicesDep, settings: SettingsDep) -> HealthResponse:
    # NFR-8: an unresolved deletion job is the evidence that a purge is incomplete,
    # so it degrades health rather than sitting in a table nobody queries.
    pending_deletions = await services.documents.pending_deletions()

    checks = {
        "db": "ok" if services.db.connected else "disconnected",
        "pending_deletions": (
            "ok"
            if pending_deletions == 0
            else f"{pending_deletions} unreconciled document deletion(s)"
        ),
        "object_store": _object_store_state(settings),
        "llm_provider": _llm_state(services, settings),
        # Off is a supported configuration, not a fault: FR-2 rejects scans by
        # default. But "enabled and unavailable" is a fault, and it degrades, because
        # otherwise a scan is rejected with the FR-2 message while the operator
        # believes OCR is running.
        "ocr": _ocr_state(services, settings),
        # The in-memory store is a supported Phase 1 configuration, not a missing
        # dependency, so it reports ok and names itself.
        "vector_store": _vector_store_state(services, settings),
    }
    # Only when that pipeline is the one serving questions. Its index is a third
    # place the text lives, and an empty one is the silent failure on this path:
    # retrieval finds nothing and every answer abstains, with nothing logged as a
    # fault. `vector_store` above cannot report it — that is the other store.
    if services.orchestrated is not None:
        checks["orchestrator"] = services.orchestrated.status()
    # Convention: a check is satisfied iff it reports "ok"; the rest of the string is
    # detail for the reader, which keeps "ok (memory…)" from reading as a fault.
    healthy = all(state.startswith("ok") for state in checks.values())
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )


@router.get("/metrics")
async def metrics() -> dict:
    return METRICS.snapshot()
