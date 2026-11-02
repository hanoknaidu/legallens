"""Request middleware: rate limiting.

`rate_limit_per_minute` has been in the configuration since the beginning and has
never been enforced, which is worse than not having it — the setting reads as a
control that exists.

In-process and per-caller, which is sound only because the service is single-worker by
design (the pending-turn registry lives in memory and SQLite takes one writer). A
second worker would give each its own allowance; that is a Redis change, and it
arrives with the same change that makes multi-worker possible at all.

**What "per-caller" means, and why it is not per-user.** Middleware runs before
dependencies, so resolving the cookie to a user id here would mean a database read on
every request, duplicating the one `deps.current_user` already does. The key is
therefore derived from the session *token* without touching the database, and an
unauthenticated request is keyed on its source address. Two consequences worth stating:

* A user with two browsers gets two allowances. For a limit that exists to stop one
  client hammering the service, that is an acceptable loosening — and the alternative
  costs a query per request forever.
* Sign-in is keyed by address, because there is no session yet. So a sign-in cannot
  consume the allowance of the session it is about to create, and a flood of sign-in
  attempts from one address is still bounded here — on top of
  `auth.throttle.SignInThrottle`, which bounds password guessing per account.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import CONTENT_TYPE
from .observability import METRICS, request_id_var

log = logging.getLogger(__name__)

#: Never limited. A monitor that gets 429'd reports an outage that is not happening,
#: and `/health` is the one endpoint that must answer when the service is struggling.
EXEMPT_PATHS = frozenset({"/api/health", "/api/metrics"})

#: Streaming a long answer holds one request open for minutes. Counting it against a
#: per-minute allowance would make a slow answer look like abuse.
EXEMPT_PREFIXES = ("/api/chat/stream",)

#: Duplicated from `api.auth` rather than imported, because importing a router module
#: here would make the middleware depend on the API layer it wraps. The two are pinned
#: together by a test.
SESSION_COOKIE = "ll_session"


class RateLimiter:
    """A sliding-window counter per owner.

    A sliding window rather than a fixed one: with fixed windows a client can send
    double the allowance across a boundary, and the burst lands exactly when the
    window resets. The deque holds one timestamp per request, which at 60/minute is
    trivial.
    """

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, owner_id: str, now: float | None = None) -> tuple[bool, int]:
        """`(allowed, retry_after_seconds)`."""
        if self._limit <= 0:
            return True, 0

        now = now if now is not None else time.monotonic()
        window = self._hits.setdefault(owner_id, deque())
        cutoff = now - 60.0
        while window and window[0] <= cutoff:
            window.popleft()

        if len(window) >= self._limit:
            # When the oldest request ages out, one slot frees up.
            retry_after = max(1, int(window[0] + 60.0 - now) + 1)
            return False, retry_after

        window.append(now)
        return True, 0

    def remaining(self, owner_id: str) -> int:
        return max(0, self._limit - len(self._hits.get(owner_id, ())))


def _caller_key(request: Request) -> str:
    """Who to count this request against.

    The session cookie is hashed rather than used raw so the key cannot leak a live
    token into a log line or a metric label. Truncated because this is a bucket key,
    not a credential check — a collision would merely share an allowance.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return "session:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def register_rate_limit(app, settings: Settings) -> None:
    limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        caller = _caller_key(request)
        allowed, retry_after = limiter.check(caller)
        if not allowed:
            METRICS.incr("rate_limited", route=path)
            log.warning("rate limited", extra={"path": path, "retry_after": retry_after})
            # problem+json like every other failure, so the client parses one shape
            # and can show the detail rather than "something went wrong".
            return JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": (
                        f"More than {settings.rate_limit_per_minute} requests in a "
                        f"minute. Try again in {retry_after}s."
                    ),
                    "code": "rate_limited",
                    "requestId": request_id_var.get(),
                },
                status_code=429,
                media_type=CONTENT_TYPE,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(limiter.remaining(caller))
        return response
