"""Sign-in, sign-out, and the current session.

Three routes, and the cookie is set and cleared in exactly this file — no other
module touches `Set-Cookie`, so the flags cannot drift apart between the two paths.

Why a cookie rather than a bearer token in a header: `HttpOnly` is the one storage
the page's own JavaScript cannot read, so a cross-site scripting bug on any route
cannot exfiltrate the session. A token in `localStorage` is readable by definition,
and for privileged attorney-client material (BRD §8.2) that is the wrong trade. The
cost is CSRF exposure, which `SameSite=Lax` plus the same-origin nginx proxy
addresses: the browser does not attach a Lax cookie to a cross-site POST.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request, Response, status

from ..auth.service import unauthenticated
from ..clock import utcnow
from ..deps import CurrentUserDep, ServicesDep, SettingsDep, session_token
from ..schemas import SessionResponse, SessionUser, SignInRequest

router = APIRouter(prefix="/auth", tags=["auth"])

#: Scoped to `/api`. The frontend calls nothing else, so the cookie has no reason to
#: ride along with a request for an image or the HTML shell.
COOKIE_NAME = "ll_session"
COOKIE_PATH = "/api"


def _set_cookie(response: Response, token: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        path=COOKIE_PATH,
        httponly=True,
        secure=secure,
        samesite="lax",
    )


@router.post("/signin", response_model=SessionResponse)
async def sign_in(
    body: SignInRequest,
    request: Request,
    response: Response,
    services: ServicesDep,
    settings: SettingsDep,
) -> SessionResponse:
    """Exchange an email and password for a session cookie.

    Every failure here is a 401 with `invalid_credentials`, or a 429 with
    `too_many_attempts`. See `auth/service.py` for why the first is deliberately
    uninformative.
    """
    user, token = await services.auth.sign_in(
        email=body.email,
        password=body.password,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_cookie(
        response,
        token,
        max_age=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
    )
    # A first sign-in on a fresh database has no matter to put documents in.
    await services.bootstrap_owner(user.id)
    return SessionResponse(
        open_signin=settings.open_signin,
        user=SessionUser(
            id=user.id,
            email=user.email,
            name=user.name,
            expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        ),
    )


@router.post("/signout", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(request: Request, response: Response, services: ServicesDep) -> Response:
    """Delete the session server-side and clear the cookie.

    204 whether or not there was a session: a client that has lost its cookie still
    wants this to succeed, and telling it "you were not signed in" helps nobody.
    """
    await services.auth.sign_out(session_token(request))
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/session", response_model=SessionResponse)
async def current_session(
    request: Request, services: ServicesDep, settings: SettingsDep
) -> SessionResponse:
    """Who is signed in, or `{"user": null}`.

    The one authenticated-ish route that does not 401. The login page calls it on
    load to decide whether to redirect away, and a 401 there would be indistinguishable
    from a server fault.
    """
    user = await services.auth.resolve(session_token(request))
    if user is None:
        return SessionResponse(user=None, open_signin=settings.open_signin)
    return SessionResponse(
        open_signin=settings.open_signin,
        user=SessionUser(
            id=user.id,
            email=user.email,
            name=user.name,
            expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        ),
    )


@router.get("/me", response_model=SessionUser)
async def me(user: CurrentUserDep, settings: SettingsDep) -> SessionUser:
    """The signed-in user, or 401. The authenticated counterpart to `/auth/session`."""
    if user is None:  # pragma: no cover - CurrentUserDep raises first
        raise unauthenticated()
    return SessionUser(
        id=user.id,
        email=user.email,
        name=user.name,
        expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
    )
