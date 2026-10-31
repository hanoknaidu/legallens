"""Request dependencies.

`current_owner_id` is the Phase 2 seam. Phase 1 has no login, so it returns the
stand-in subject — but it is a dependency rather than a constant inlined in queries,
which is what lets authentication become a change to this one function (BRD §6.1).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from .config import PHASE1_OWNER_ID, Settings
from .services import Services


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_services(request: Request) -> Services:
    return request.app.state.services


def current_owner_id() -> str:
    return PHASE1_OWNER_ID


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ServicesDep = Annotated[Services, Depends(get_services)]
OwnerDep = Annotated[str, Depends(current_owner_id)]
