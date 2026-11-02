"""Sign-in, sign-out, and resolving a cookie to a user.

The rules this file enforces, each of which is a decision rather than a default:

* **One failure message.** A wrong password, an unknown address and a disabled
  account all return `invalid_credentials`. Which of the three it was is in the log,
  not in the response: for a legal product, "does this person have an account here?"
  is itself privileged (BRD §8.2).
* **A fixed cost per attempt.** A missing account still pays for one scrypt verify
  against `dummy_hash`, so response time does not answer that question either.
* **A new token per sign-in.** Never reuse or extend the caller's existing cookie —
  that is session fixation, where an attacker plants a known token and waits for
  someone to authenticate it.
* **Sign-out deletes the row.** Not a flag, not an expiry bump.
"""

from __future__ import annotations

import logging

from ..config import PHASE1_OWNER_ID
from ..db.repositories.users import User, UserRepository
from ..errors import ApiProblem
from .passwords import dummy_hash, hash_password, new_session_token, token_hash, verify_password
from .throttle import SignInThrottle

log = logging.getLogger(__name__)

#: The id Phase 1 wrote on every row. The first user adopts it so that documents,
#: conversations and audit entries created before there were accounts stay reachable
#: by the person who created them (schema.sql, BRD §6.1). Aliased rather than
#: redefined, so there is one literal.
LEGACY_OWNER_ID = PHASE1_OWNER_ID


def invalid_credentials() -> ApiProblem:
    return ApiProblem(
        401,
        "Sign-in failed",
        "Email or password is incorrect.",
        "invalid_credentials",
    )


def unauthenticated() -> ApiProblem:
    return ApiProblem(
        401,
        "Not signed in",
        "This request needs a signed-in session.",
        "unauthenticated",
    )


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        *,
        session_ttl_seconds: int,
        scrypt_n: int,
        throttle: SignInThrottle | None = None,
    ) -> None:
        self._users = users
        self._ttl = session_ttl_seconds
        self._scrypt_n = scrypt_n
        self.throttle = throttle or SignInThrottle()

    # -- accounts -----------------------------------------------------------

    async def create_user(self, *, email: str, name: str, password: str) -> User:
        """Create an account. Raises `ApiProblem` on a duplicate or a weak password.

        There is no public sign-up route that reaches this. Accounts are made by an
        operator (`python -m legal_lens.auth.cli`) or by first-run bootstrap, because
        a firm's document library is not something strangers should be able to join.
        """
        email = self._users.normalise_email(email)
        if not email or "@" not in email:
            raise ApiProblem(422, "Invalid email", "That is not an email address.", "invalid_email")
        if len(password) < 12:
            # Length is the only requirement. Composition rules ("one capital, one
            # digit") push people towards P@ssw0rd1 and are no longer recommended by
            # NIST (SP 800-63B); length is what actually costs an attacker.
            raise ApiProblem(
                422,
                "Password too short",
                "Use at least 12 characters.",
                "password_too_short",
            )
        if await self._users.by_email(email) is not None:
            raise ApiProblem(
                409, "Already registered", "That email already has an account.", "email_taken"
            )

        # The first account adopts the Phase 1 owner id, so pre-auth data is not
        # orphaned. Everyone after gets a generated one.
        first = await self._users.count() == 0
        return await self._users.create(
            email=email,
            name=name,
            password_hash=hash_password(password, n=self._scrypt_n),
            user_id=LEGACY_OWNER_ID if first else None,
        )

    async def set_password(self, user: User, password: str) -> None:
        """Change a password and end every session, including the caller's.

        A password change that leaves old sessions alive does not accomplish what
        people change passwords for.
        """
        if len(password) < 12:
            raise ApiProblem(
                422, "Password too short", "Use at least 12 characters.", "password_too_short"
            )
        await self._users.set_password(user.id, hash_password(password, n=self._scrypt_n))
        await self._users.delete_sessions_for_user(user.id)

    # -- sign in / out ------------------------------------------------------

    async def sign_in(
        self, *, email: str, password: str, user_agent: str | None, ip: str | None
    ) -> tuple[User, str]:
        """`(user, session_token)`. The caller sets the cookie; this mints the session."""
        throttle_key = f"{self._users.normalise_email(email)}|{ip or '-'}"
        retry_after = self.throttle.check(throttle_key)
        if retry_after:
            raise ApiProblem(
                429,
                "Too many attempts",
                f"Too many sign-in attempts. Try again in {retry_after}s.",
                "too_many_attempts",
                retryAfter=retry_after,
            )

        user = await self._users.by_email(email)
        if user is None or not user.is_active:
            # Pay the cost anyway — see the module docstring.
            verify_password(password, dummy_hash(self._scrypt_n))
            self.throttle.record_failure(throttle_key)
            log.warning(
                "sign-in rejected",
                extra={"reason": "no_such_user" if user is None else "inactive"},
            )
            raise invalid_credentials()

        if not verify_password(password, user.password_hash):
            self.throttle.record_failure(throttle_key)
            log.warning("sign-in rejected", extra={"reason": "bad_password", "user_id": user.id})
            raise invalid_credentials()

        self.throttle.record_success(throttle_key)
        token = new_session_token()
        await self._users.create_session(
            token_hash=token_hash(token),
            user_id=user.id,
            ttl_seconds=self._ttl,
            user_agent=user_agent,
            ip=ip,
        )
        await self._users.touch_login(user.id)
        log.info("sign-in", extra={"user_id": user.id})
        return user, token

    async def sign_out(self, token: str | None) -> None:
        """Idempotent: signing out without a session is a success, not a 401."""
        if token:
            await self._users.delete_session(token_hash(token))

    async def resolve(self, token: str | None) -> User | None:
        """The user behind a cookie, sliding the session window.

        Returns None rather than raising, because one caller (`/auth/session`) wants
        to know whether anyone is signed in without that being an error.
        """
        if not token:
            return None
        digest = token_hash(token)
        user = await self._users.session_user(digest)
        if user is None:
            return None
        await self._users.touch_session(digest, ttl_seconds=self._ttl)
        return user
