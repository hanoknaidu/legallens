from __future__ import annotations

import uuid

import aiosqlite

from ...clock import iso
from ...schemas import Matter
from ..session import Database

# FR-4 seeds one matter on first boot, and its id is load-bearing: the frontend falls
# back to this literal when no matter is selected, so a different id silently breaks
# conversation creation. See integration-TODO.md G8.
DEFAULT_MATTER_ID = "m-001"
DEFAULT_MATTER_NAME = "Northwind — Vendor Agreements"
DEFAULT_MATTER_CLIENT = "Northwind Logistics, Inc."


def _to_matter(row: aiosqlite.Row) -> Matter:
    return Matter(id=row["id"], name=row["name"], client=row["client"])


class MatterRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, owner_id: str) -> list[Matter]:
        rows = await self._db.fetch_all(
            "SELECT id, name, client FROM matters WHERE owner_id = ? ORDER BY created_at",
            (owner_id,),
        )
        return [_to_matter(r) for r in rows]

    async def exists(self, owner_id: str, matter_id: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 FROM matters WHERE id = ? AND owner_id = ?", (matter_id, owner_id)
        )
        return row is not None

    async def ensure_default(self, owner_id: str) -> str:
        """Seed the default matter if this owner has none. Idempotent."""
        row = await self._db.fetch_one(
            "SELECT id FROM matters WHERE owner_id = ? ORDER BY created_at LIMIT 1",
            (owner_id,),
        )
        if row is not None:
            return row["id"]

        # `m-001` goes to whoever seeds first — in Phase 1 that is the only owner, and
        # the frontend's fallback depends on the literal. The id is a global primary
        # key, though, not a per-owner one, so a second owner has to get a generated
        # id; hardcoding it would make Phase 2's first new owner a UNIQUE violation.
        matter_id = DEFAULT_MATTER_ID
        if await self._db.fetch_one("SELECT 1 FROM matters WHERE id = ?", (DEFAULT_MATTER_ID,)):
            matter_id = f"m-{uuid.uuid4().hex[:8]}"

        await self._db.execute(
            "INSERT INTO matters (id, owner_id, name, client, created_at) VALUES (?, ?, ?, ?, ?)",
            (matter_id, owner_id, DEFAULT_MATTER_NAME, DEFAULT_MATTER_CLIENT, iso()),
        )
        return matter_id
