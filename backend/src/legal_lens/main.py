"""Application entrypoint — `uvicorn legal_lens.main:app`.

The service boots with no API key, no Qdrant, no orchestrator and no frontend
(§13.1). Nothing here reaches a network on startup, because a service that cannot
start without a vendor cannot be developed offline and cannot be demonstrated when
the vendor is down. Missing capability is reported by `GET /health` as `degraded` — a
200 that names what is absent — rather than by refusing to listen.

Routers mount under `/api`, which is what makes the frontend's integration a Vite
proxy rather than a base-URL change.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import audit, chat, conversations, documents, exports, matters, platform
from .config import PHASE1_OWNER_ID, Settings, get_settings
from .db.session import Database
from .errors import register_error_handlers
from .middleware import register_rate_limit
from .observability import METRICS, configure_logging, new_request_id, request_id_var
from .services import Services

log = logging.getLogger(__name__)

API_PREFIX = "/api"
ROUTERS = (
    platform.router,
    matters.router,
    conversations.router,
    documents.router,
    chat.router,
    exports.router,
    audit.router,
)

# Metric keys have to stay bounded, so they are built from the matched route's
# template rather than the concrete URL: /conversations/{id} is one series, not one
# per conversation. `include_router` keeps the sub-router's own unprefixed path, so
# the prefix is prepended here. An unmatched path has no template at all and is
# counted under one key — otherwise anything scanning for /wp-login.php could grow
# the metric table without bound.
UNMATCHED_ROUTE = "<unmatched>"


def _metric_route(request: Request) -> str:
    template = getattr(request.scope.get("route"), "path_format", None)
    return f"{API_PREFIX}{template}" if template else UNMATCHED_ROUTE


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the resources whose lifetime is the process's.

    The database connection is opened once here rather than per request: SQLite takes
    a single writer, and `Database` serialises statements through a lock that only
    works if every caller shares the one connection.
    """
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)

    db = Database(settings.db_path)
    await db.connect()
    app.state.db = db

    services = Services(db, settings)
    app.state.services = services
    await services.bootstrap(PHASE1_OWNER_ID)

    # Ingestion writes original bytes here, and a failure to write is only
    # discoverable at upload time otherwise — by which point the user has waited
    # through a 50MB POST. /health probes it instead.
    settings.objects_path.mkdir(parents=True, exist_ok=True)

    log.info(
        "service started",
        extra={
            "environment": settings.environment,
            "db": str(settings.db_path),
            "llm_provider": settings.llm_provider,
            "vector_store": settings.vector_store,
            "embedding_provider": settings.embedding_provider,
        },
    )
    try:
        yield
    finally:
        await services.shutdown()
        await db.close()
        log.info("service stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Legal Lens API",
        version=__version__,
        lifespan=lifespan,
        # The frontend is the only client and it codes against the schemas module;
        # the interactive docs are for development, so they follow the environment.
        docs_url="/api/docs" if settings.environment != "prod" else None,
        openapi_url="/api/openapi.json" if settings.environment != "prod" else None,
    )
    app.state.settings = settings

    # Credentials off: Phase 1 has no session, so this list bounds which origins may
    # call the API, not which may carry a cookie (config.cors_origins). In dev the
    # frontend comes through the Vite proxy and is same-origin, so this is a
    # belt-and-braces bound rather than the thing holding the browser back.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Request id, timing and the access log in one pass (NFR-13).

        The id is bound to a context var so every log record emitted downstream
        carries it without being passed one, and returned on the response so a
        user-visible failure can be traced to the line that explains it. Moves to
        middleware.py when rate limiting joins it.
        """
        request_id = request.headers.get("x-request-id") or new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers turn this into a problem response; the metric and
            # the log line still have to happen, so they happen here.
            route = _metric_route(request)
            METRICS.incr("requests", route=route, status=500)
            METRICS.observe("request_ms", (time.perf_counter() - started) * 1000, route=route)
            log.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        finally:
            request_id_var.reset(token)

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = _metric_route(request)
        METRICS.incr("requests", route=route, status=response.status_code)
        METRICS.observe("request_ms", elapsed_ms, route=route)
        response.headers["X-Request-Id"] = request_id
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round(elapsed_ms, 2),
            },
        )
        return response

    # Registered after the request-context middleware so it runs *inside* it and a
    # 429 still carries a request id and is counted.
    register_rate_limit(app, settings)

    register_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
