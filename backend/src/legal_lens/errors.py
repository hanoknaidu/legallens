"""Problem responses (§5.8).

Every failure crosses the wire in one shape, so the frontend has one thing to parse
and one place to find a message worth showing a user. Two fields carry the weight:
`detail` is written to be read by a person — the FR-2 rejection reason, the name of
the document that is not ready — and `code` is the stable string a client branches on
when it needs to behave differently rather than merely say something.

`requestId` is the same id bound to the log context, so a failure a user reports can
be traced to the line that explains it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .observability import request_id_var

log = logging.getLogger(__name__)

CONTENT_TYPE = "application/problem+json"


class ApiProblem(Exception):
    """A failure with a message worth showing the user.

    Raised instead of `HTTPException` wherever the client can do something with the
    detail — which, in this service, is most places.
    """

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        code: str,
        **extra: Any,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.code = code
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {
            "type": "about:blank",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "code": self.code,
            "requestId": request_id_var.get(),
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Constructors for the failures that recur. Naming them here keeps wording and
# status codes consistent across routers, and keeps `code` from drifting.
# ---------------------------------------------------------------------------


def not_found(what: str, identifier: str) -> ApiProblem:
    return ApiProblem(
        status.HTTP_404_NOT_FOUND,
        f"{what.capitalize()} not found",
        f"No {what} with id {identifier}.",
        f"{what.replace(' ', '_')}_not_found",
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def _problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code, media_type=CONTENT_TYPE)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Mostly 404s from unrouted paths and the odd `HTTPException` from a
        # dependency. Normalised into the same shape so the client parses one thing.
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Request failed",
                "status": exc.status_code,
                "detail": str(exc.detail),
                "code": "http_error",
                "requestId": request_id_var.get(),
            },
            status_code=exc.status_code,
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # The first error only. A user cannot act on seven of them, and the full list
        # is in the log line this response's requestId points at.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query"))
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Request is not valid",
                "status": 422,
                "detail": f"{field}: {first.get('msg', 'invalid')}"
                if field
                else first.get("msg", "invalid request"),
                "code": "validation_error",
                "requestId": request_id_var.get(),
            },
            status_code=422,
            media_type=CONTENT_TYPE,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Nothing internal crosses the wire: a stack trace tells an attacker about the
        # stack and a user nothing. The requestId ties this to the logged traceback.
        log.exception("unhandled exception", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Internal error",
                "status": 500,
                "detail": "The request could not be completed.",
                "code": "internal_error",
                "requestId": request_id_var.get(),
            },
            status_code=500,
            media_type=CONTENT_TYPE,
        )
