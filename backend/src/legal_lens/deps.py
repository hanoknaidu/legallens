"""Request dependencies.

`current_owner_id` was the Phase 2 seam the BRD promised (§6.1): "authentication
becomes a middleware and frontend change rather than a schema migration". This is that
change. It used to return a constant; it now resolves the session cookie to a user id
and raises 401 when there is none. Every router that already took `OwnerDep` became
authenticated without being edited, and every query was already filtering on the value
it returns.

The two dependencies differ in what they hand back, not in what they enforce — both
require a live session:

* `OwnerDep` — the id, which is what repositories filter on. Used by every data route.
* `CurrentUserDep` — the whole user, for the routes that show an email or a name.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from .auth.service import unauthenticated
from .config import PHASE1_OWNER_ID, Settings
from .db.repositories.users import User
from .services import Services


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_services(request: Request) -> Services:
    return request.app.state.services


def session_token(request: Request) -> str | None:
    """The raw cookie value, or None.

    Imported by `api/auth.py` as well, so the cookie name has one definition. It lives
    here rather than there to avoid a circular import: `api.auth` imports from `deps`.
    """
    return request.cookies.get("ll_session")


#: Who every request belongs to when `require_auth` is off. Not a row in `users` —
#: nothing is written, nothing can sign in as it, and it exists only so the code paths
#: below have a `User` to hand back. Its id is the one Phase 1 wrote on every row, so
#: a showcase build sees the data a pre-authentication build saw.
DEMO_USER = User(
    id=PHASE1_OWNER_ID,
    email="demo@legal-lens.local",
    name="Demo",
    password_hash="",
    is_active=True,
)


async def current_user(request: Request) -> User:
    """The signed-in user, or 401.

    Resolving touches the session row, which slides the idle window — so a user who
    keeps working keeps their session, and one who stops loses it after
    `session_ttl_seconds`.

    With `require_auth` off this is a showcase build: there is no check and every
    request is the demo user. A real session still wins if one is present, so turning
    the flag on mid-session does not sign anybody out.
    """
    services: Services = request.app.state.services
    settings: Settings = request.app.state.settings

    user = await services.auth.resolve(session_token(request))
    if user is not None:
        return user
    if not settings.require_auth:
        return DEMO_USER
    raise unauthenticated()


async def current_owner_id(user: Annotated[User, Depends(current_user)]) -> str:
    return user.id


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ServicesDep = Annotated[Services, Depends(get_services)]
OwnerDep = Annotated[str, Depends(current_owner_id)]
CurrentUserDep = Annotated[User, Depends(current_user)]
