from __future__ import annotations

from fastapi import APIRouter

from ..deps import OwnerDep, ServicesDep
from ..schemas import Matter

router = APIRouter(tags=["matters"])


@router.get("/matters", response_model=list[Matter])
async def list_matters(services: ServicesDep, owner_id: OwnerDep) -> list[Matter]:
    return await services.matters.list(owner_id)
