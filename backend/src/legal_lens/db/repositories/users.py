"""Users and sessions.

Two tables, one repository: a session is meaningless without the user it belongs to,
and every read of one needs the other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import aiosqlite

from ...clock import iso, utcnow
from ..session import Database


@dataclass(frozen=True, slots=True)
class User:
    """A user as the service uses one. Never crosses the wire — `SessionUser` does.

    `password_hash` is on this object because `authenticate` needs it, and is why this
    type stays inside the service boundary.
    """

    id: str
    email: str
    name: str
    password_hash: str
    is_active: bool


def _to_user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        password_hash=row["password_hash"],
        is_active=bool(row["is_active"]),
    )


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- users --------------------------------------------------------------

    @staticmethod
    def normalise_email(email: str) -> str:
        """Lowercased and trimmed. Addresses are not case-sensitive in practice, and a
        user who signs up as `A@firm.com` and signs in as `a@firm.com` is the same
        person — so the normalisation happens here rather than at each call site."""
        return email.strip().lower()

    async def count(self) -> int:
        row = await self._db.fetch_one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    async def by_email(self, email: str) -> User | None:
        row = await self._db.fetch_one(
            "SELECT id, email, name, password_hash, is_active FROM users WHERE email = ?",
            (self.normalise_email(email),),
        )
        return _to_user(row) if row else None

    async def by_id(self, user_id: str) -> User | None:
        row = await self._db.fetch_one(
            "SELECT id, email, name, password_hash, is_active FROM users WHERE id = ?",
            (user_id,),
        )
        return _to_user(row) if row else None

    async def create(
        self, *, email: str, name: str, password_hash: str, user_id: str | None = None
    ) -> User:
        """Insert a user. The caller supplies an already-hashed password.

        A plaintext password is never a parameter here: a repository that could hash
        would be a repository that could forget to.
        """
        email = self.normalise_email(email)
        user = User(
            id=user_id or f"u-{uuid.uuid4().hex[:12]}",
            email=email,
            name=name.strip() or email,
            password_hash=password_hash,
            is_active=True,
        )
        await self._db.execute(
            "INSERT INTO users (id, email, name, password_hash, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (user.id, user.email, user.name, user.password_hash, iso()),
        )
        return user

    async def touch_login(self, user_id: str) -> None:
        await self._db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (iso(), user_id))

    async def set_password(self, user_id: str, password_hash: str) -> None:
        await self._db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )

    # -- sessions -----------------------------------------------------------

    async def create_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        ttl_seconds: int,
        user_agent: str | None,
        ip: str | None,
    ) -> None:
        issued = utcnow()
        await self._db.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at, "
            "user_agent, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash,
                user_id,
                iso(issued),
                iso(issued + timedelta(seconds=ttl_seconds)),
                iso(issued),
                (user_agent or "")[:256] or None,
                ip,
            ),
        )

    async def session_user(self, token_hash: str) -> User | None:
        """The user behind a live session, or None.

        The expiry is compared in SQL rather than in Python so there is exactly one
        definition of "live", and it is the same one the cleanup uses. An expired row
        is not deleted here — a read path that writes would serialise every request
        behind SQLite's single writer.
        """
        row = await self._db.fetch_one(
            "SELECT u.id, u.email, u.name, u.password_hash, u.is_active "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1",
            (token_hash, iso()),
        )
        return _to_user(row) if row else None

    async def touch_session(self, token_hash: str, *, ttl_seconds: int) -> None:
        """Slide the window. Keeps a working session alive and lets an idle one lapse."""
        current = utcnow()
        await self._db.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
            (iso(current), iso(current + timedelta(seconds=ttl_seconds)), token_hash),
        )

    async def delete_session(self, token_hash: str) -> None:
        await self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    async def delete_sessions_for_user(self, user_id: str) -> None:
        """Every session, everywhere. What a password change has to do."""
        await self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    async def purge_expired_sessions(self) -> int:
        return await self._db.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso(),))
