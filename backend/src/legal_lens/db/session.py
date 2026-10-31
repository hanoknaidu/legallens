"""SQLite connection management.

One connection, serialised through a lock. SQLite allows a single writer, and the
alternative — a pool that intermittently raises `database is locked` under the 20
concurrent users of NFR-4 — trades a predictable bottleneck for an unpredictable
failure. The lock is held only for the duration of a statement; the long waits in this
service are model calls, which hold no database connection.

Every query takes `owner_id` (BRD §6.1). That is enforced by the repository, not here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        # Rather than failing immediately on contention, wait: a writer finishes in
        # milliseconds and the caller is an HTTP request that can afford to queue.
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.executescript(SCHEMA_PATH.read_text())
        await self._migrate(conn)
        await conn.commit()
        self._conn = conn

    @staticmethod
    async def _migrate(conn: aiosqlite.Connection) -> None:
        """Additive migrations for databases created before a column existed.

        `schema.sql` is all `CREATE TABLE IF NOT EXISTS`, so adding a column to that
        file only affects *new* databases — an existing one silently keeps the old
        shape and every query naming the new column fails at runtime. These run after
        the schema and are idempotent, so a fresh database is unaffected.
        """
        migrations: list[tuple[str, str, str]] = [
            # (table, column, DDL)
            (
                "documents",
                "text_source",
                "ALTER TABLE documents ADD COLUMN text_source TEXT NOT NULL DEFAULT 'native'",
            ),
            # Nullable rather than defaulted: a row written before these existed did
            # not record them, and `0`/`''` would read as "partial coverage, no
            # rewrite" — a claim about a turn nobody measured. NULL says unknown.
            (
                "audit_log",
                "coverage",
                "ALTER TABLE audit_log ADD COLUMN coverage INTEGER",
            ),
            (
                "audit_log",
                "rewrite",
                "ALTER TABLE audit_log ADD COLUMN rewrite TEXT",
            ),
        ]
        for table, column, ddl in migrations:
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            if column not in existing:
                log.info("applying migration", extra={"table": table, "column": column})
                await conn.execute(ddl)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connected(self) -> bool:
        return self._conn is not None

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        async with self._lock:
            yield self._conn

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self._cursor() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
            return list(rows)

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self._cursor() as conn:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            return row

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._cursor() as conn:
            cursor = await conn.execute(sql, params)
            await conn.commit()
            count = cursor.rowcount
            await cursor.close()
            return count

    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        batch = list(rows)
        if not batch:
            return
        async with self._cursor() as conn:
            await conn.executemany(sql, batch)
            await conn.commit()

    async def execute_script(self, statements: Sequence[tuple[str, Sequence[Any]]]) -> None:
        """Run several statements in one transaction.

        Used where a partial write would be worse than no write — persisting an
        assistant turn and its citations, for instance: a message whose citations are
        missing renders as an uncited factual assertion, which NFR-1 forbids.
        """
        async with self._cursor() as conn:
            try:
                for sql, params in statements:
                    await conn.execute(sql, params)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


def to_json(value: Any) -> str:
    # sort_keys so a cached prompt prefix built from this text stays byte-stable.
    return json.dumps(value, default=str, sort_keys=True)


def from_json(raw: str | None, fallback: Any = None) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback
