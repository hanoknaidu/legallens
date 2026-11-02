"""Authentication (BRD §6.1, Phase 2).

What these tests are for: the BRD's claim that Phase 1's missing login was "a
deliberate scope cut, not an oversight" rested on the data model carrying `owner_id`
everywhere so that Phase 2 would be a middleware change. These check that the
middleware change actually holds the line — most importantly that one user cannot see
another's documents, which is the whole point of the owner column.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import TEST_EMAIL, TEST_PASSWORD
from fixtures import text_pdf
from helpers import MSA_PAGES
from legal_lens.auth.passwords import hash_password, token_hash, verify_password
from legal_lens.auth.throttle import SignInThrottle
from legal_lens.config import PHASE1_OWNER_ID, Settings
from legal_lens.db.repositories.users import UserRepository
from legal_lens.errors import ApiProblem
from legal_lens.main import create_app

SECOND_EMAIL = "opposing@counsel.test"
SECOND_PASSWORD = "a-different-long-password"


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_a_password_verifies_against_its_own_hash() -> None:
    encoded = hash_password("correct horse battery staple", n=2**12)
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("Correct horse battery staple", encoded)


def test_the_hash_carries_its_own_parameters() -> None:
    """So raising the cost later does not invalidate hashes written under the old one."""
    encoded = hash_password("x" * 12, n=2**12)
    algorithm, n, r, p, _salt, _key = encoded.split("$")
    assert (algorithm, n, r, p) == ("scrypt", "4096", "8", "1")
    # The same password hashed twice differs: the salt is per-hash, so a stolen table
    # cannot be attacked once for every user who chose the same password.
    assert hash_password("x" * 12, n=2**12) != encoded


def test_a_corrupt_hash_fails_rather_than_raising() -> None:
    """A column holding something unexpected must read as "does not match" — a 500
    would tell the caller the account exists and its record is broken."""
    for broken in ("", "not-a-hash", "scrypt$x$8$1$aaaa$bbbb", "argon2$1$2$3$aa$bb"):
        assert not verify_password("anything", broken)


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


async def test_signing_in_sets_an_httponly_session_cookie(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    await app.state.services.auth.create_user(
        email=TEST_EMAIL, name="Test Attorney", password=TEST_PASSWORD
    )
    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == TEST_EMAIL
    cookie = response.headers["set-cookie"]
    # HttpOnly is the one property that makes an XSS bug anywhere in the app unable to
    # read the session; SameSite is what stands in for CSRF tokens on a same-origin app.
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    assert "Path=/api" in cookie
    # Not Secure in test/dev: a Secure cookie is never sent over plain HTTP, and the
    # symptom is a sign-in that appears to work and then does nothing.
    assert "Secure" not in cookie


async def test_the_session_cookie_is_secure_in_prod(tmp_path) -> None:
    from legal_lens.config import Settings
    from legal_lens.main import create_app

    settings = Settings(environment="prod", data_dir=tmp_path, llm_provider="echo", scrypt_n=2**12)
    assert settings.cookie_secure is True

    app = create_app(settings)
    from httpx import ASGITransport

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        await app.state.services.auth.create_user(
            email=TEST_EMAIL, name="T", password=TEST_PASSWORD
        )
        response = await client.post(
            "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        assert "Secure" in response.headers["set-cookie"]


async def test_a_wrong_password_is_refused_without_detail(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """The message never says *which* part was wrong.

    Under `open_signin=False` an unknown address gets this same reply, so the endpoint
    cannot be used to ask "is this person a client of this firm?" — which for a legal
    product is itself privileged (BRD §8.2). See
    `test_strict_mode_refuses_an_unknown_address`.
    """
    await app.state.services.auth.create_user(
        email=TEST_EMAIL, name="Test Attorney", password=TEST_PASSWORD
    )
    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": "wrong-password-entirely"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "invalid_credentials"
    assert body["detail"] == "Email or password is incorrect."
    assert "set-cookie" not in response.headers


async def test_a_disabled_account_cannot_sign_in(app: FastAPI, anon_client: AsyncClient) -> None:
    services = app.state.services
    user = await services.auth.create_user(
        email=TEST_EMAIL, name="Test Attorney", password=TEST_PASSWORD
    )
    await services.db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))

    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    # Same message as every other failure: that the account exists but is disabled is
    # not something the sign-in form should disclose.
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_the_email_is_not_case_sensitive(app: FastAPI, anon_client: AsyncClient) -> None:
    await app.state.services.auth.create_user(
        email="Attorney@Firm.Test", name="T", password=TEST_PASSWORD
    )
    response = await anon_client.post(
        "/auth/signin", json={"email": "ATTORNEY@firm.test", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200


async def test_repeated_failures_lock_the_account_out(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    await app.state.services.auth.create_user(email=TEST_EMAIL, name="T", password=TEST_PASSWORD)
    wrong = {"email": TEST_EMAIL, "password": "not-the-password"}

    for _ in range(5):
        assert (await anon_client.post("/auth/signin", json=wrong)).status_code == 401

    locked = await anon_client.post("/auth/signin", json=wrong)
    assert locked.status_code == 429
    assert locked.json()["code"] == "too_many_attempts"

    # And the lockout is on the attempt, not on the password: the right one is refused
    # too, which is what stops an attacker using the lockout as an oracle.
    right = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert right.status_code == 429


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/matters"),
        ("get", "/conversations"),
        ("get", "/documents"),
        ("get", "/audit"),
        ("post", "/conversations"),
    ],
)
async def test_a_protected_route_refuses_an_anonymous_caller(
    anon_client: AsyncClient, method: str, path: str
) -> None:
    response = await getattr(anon_client, method)(path)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_health_stays_open(anon_client: AsyncClient) -> None:
    """A monitor has no session, and a health check that needs one reports an outage
    whenever authentication breaks — which is exactly when it must still answer."""
    assert (await anon_client.get("/health")).status_code == 200


async def test_the_session_route_answers_without_a_session(anon_client: AsyncClient) -> None:
    """The login page asks "is anyone signed in?" on load. 401 there would be
    indistinguishable from a server fault."""
    response = await anon_client.get("/auth/session")
    assert response.status_code == 200
    assert response.json()["user"] is None


async def test_a_signed_in_caller_is_let_through(client: AsyncClient) -> None:
    assert (await client.get("/matters")).status_code == 200
    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == TEST_EMAIL


# ---------------------------------------------------------------------------
# Sign-out and session lifetime
# ---------------------------------------------------------------------------


async def test_signing_out_ends_the_session_server_side(app: FastAPI, client: AsyncClient) -> None:
    """The reason sessions are a table and not a JWT: this has to actually revoke."""
    token = client.cookies.get("ll_session")
    assert token is not None
    users = UserRepository(app.state.services.db)
    assert await users.session_user(token_hash(token)) is not None

    assert (await client.post("/auth/signout")).status_code == 204

    # Gone from the database, not merely forgotten by the browser — so a copy of the
    # cookie taken before sign-out is worthless.
    assert await users.session_user(token_hash(token)) is None
    client.cookies.set("ll_session", token, domain="test", path="/api")
    assert (await client.get("/matters")).status_code == 401


async def test_signing_out_without_a_session_is_not_an_error(anon_client: AsyncClient) -> None:
    assert (await anon_client.post("/auth/signout")).status_code == 204


async def test_an_expired_session_is_refused(app: FastAPI, client: AsyncClient) -> None:
    token = client.cookies.get("ll_session")
    assert token is not None
    # Reach past the TTL rather than waiting eight hours for it.
    await app.state.services.db.execute(
        "UPDATE sessions SET expires_at = '2020-01-01T00:00:00.000000Z' WHERE token_hash = ?",
        (token_hash(token),),
    )
    assert (await client.get("/matters")).status_code == 401


async def test_using_a_session_slides_its_expiry(app: FastAPI, client: AsyncClient) -> None:
    """An idle timeout, not a hard cap: an attorney working through a long document
    should not be signed out mid-draft."""
    db = app.state.services.db
    digest = token_hash(client.cookies.get("ll_session"))
    before = (
        await db.fetch_one("SELECT expires_at FROM sessions WHERE token_hash = ?", (digest,))
    )["expires_at"]

    await db.execute(
        "UPDATE sessions SET expires_at = '2030-01-01T00:00:00.000000Z' WHERE token_hash = ?",
        (digest,),
    )
    await client.get("/matters")

    after = (await db.fetch_one("SELECT expires_at FROM sessions WHERE token_hash = ?", (digest,)))[
        "expires_at"
    ]
    # Reset to now+TTL, so the hand-planted far-future value is gone.
    assert after != "2030-01-01T00:00:00.000000Z"
    assert after != before


async def test_a_forged_cookie_is_refused(anon_client: AsyncClient) -> None:
    anon_client.cookies.set("ll_session", "a" * 43, domain="test", path="/api")
    assert (await anon_client.get("/matters")).status_code == 401


async def test_signing_in_again_mints_a_new_token(app: FastAPI, client: AsyncClient) -> None:
    """Never extend or reuse the caller's cookie: an attacker who can plant one and
    wait for the victim to authenticate it owns the session (session fixation)."""
    first = client.cookies.get("ll_session")
    response = await client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    assert client.cookies.get("ll_session") != first


async def test_changing_a_password_ends_every_session(app: FastAPI, client: AsyncClient) -> None:
    services = app.state.services
    user = await services.users.by_email(TEST_EMAIL)
    assert user is not None

    await services.auth.set_password(user, "an-entirely-new-long-password")

    assert (await client.get("/matters")).status_code == 401


# ---------------------------------------------------------------------------
# The point of the owner column: isolation
# ---------------------------------------------------------------------------


async def test_the_first_account_adopts_the_phase_one_owner_id(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """So documents uploaded before there were accounts are not orphaned."""
    user = await app.state.services.auth.create_user(
        email=TEST_EMAIL, name="T", password=TEST_PASSWORD
    )
    assert user.id == PHASE1_OWNER_ID


async def test_a_second_account_gets_its_own_id_and_matter(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    services = app.state.services
    first = await services.auth.create_user(email=TEST_EMAIL, name="A", password=TEST_PASSWORD)
    second = await services.auth.create_user(email=SECOND_EMAIL, name="B", password=SECOND_PASSWORD)
    assert second.id != first.id

    # `m-001` is taken by the first owner, so the second gets a generated id rather
    # than a UNIQUE violation.
    first_matter = await services.bootstrap_owner(first.id)
    second_matter = await services.bootstrap_owner(second.id)
    assert first_matter != second_matter


async def test_one_user_cannot_see_another_users_documents(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """The test the BRD's §6.1 scope cut was betting on.

    Every repository method takes `owner_id` and every statement filters on it. This
    drives that through the real HTTP boundary with two real sessions: a document
    uploaded by one attorney must not appear in the other's library, and must not be
    fetchable even by id.
    """
    services = app.state.services
    await services.auth.create_user(email=TEST_EMAIL, name="A", password=TEST_PASSWORD)
    await services.auth.create_user(email=SECOND_EMAIL, name="B", password=SECOND_PASSWORD)

    # Attorney A uploads.
    await anon_client.post("/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    created = await anon_client.post(
        "/documents", files={"file": ("msa.pdf", text_pdf(MSA_PAGES), "application/pdf")}
    )
    assert created.status_code in (200, 201), created.text
    document_id = created.json()["id"]
    assert len((await anon_client.get("/documents")).json()) == 1

    # Attorney B signs in on the same client, replacing the cookie.
    await anon_client.post(
        "/auth/signin", json={"email": SECOND_EMAIL, "password": SECOND_PASSWORD}
    )
    assert (await anon_client.get("/documents")).json() == []

    # Not merely hidden from the list — unreachable by id, which is the failure that
    # matters if an id ever leaks.
    assert (await anon_client.get(f"/documents/{document_id}")).status_code == 404
    assert (await anon_client.get(f"/documents/{document_id}/status")).status_code == 404


async def test_conversations_are_not_shared_between_users(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    services = app.state.services
    await services.auth.create_user(email=TEST_EMAIL, name="A", password=TEST_PASSWORD)
    await services.auth.create_user(email=SECOND_EMAIL, name="B", password=SECOND_PASSWORD)

    await anon_client.post("/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    made = await anon_client.post("/conversations", json={"title": "A's matter"})
    assert made.status_code in (200, 201), made.text
    conversation_id = made.json()["id"]

    await anon_client.post(
        "/auth/signin", json={"email": SECOND_EMAIL, "password": SECOND_PASSWORD}
    )
    assert (await anon_client.get("/conversations")).json() == []

    # There is no GET for a single conversation, so the reachable-by-id check goes
    # through the routes that do exist: reading its messages, renaming it, deleting it.
    # All three must 404 rather than touch another owner's row.
    assert (await anon_client.get(f"/conversations/{conversation_id}/messages")).status_code == 404
    assert (
        await anon_client.patch(f"/conversations/{conversation_id}", json={"title": "mine now"})
    ).status_code == 404
    assert (await anon_client.delete(f"/conversations/{conversation_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Account creation rules
# ---------------------------------------------------------------------------


async def test_a_duplicate_email_is_refused(app: FastAPI, anon_client: AsyncClient) -> None:
    services = app.state.services
    await services.auth.create_user(email=TEST_EMAIL, name="A", password=TEST_PASSWORD)
    with pytest.raises(ApiProblem) as caught:
        await services.auth.create_user(email=TEST_EMAIL, name="B", password=SECOND_PASSWORD)
    assert caught.value.code == "email_taken"


async def test_a_short_password_is_refused(app: FastAPI, anon_client: AsyncClient) -> None:
    with pytest.raises(ApiProblem) as caught:
        await app.state.services.auth.create_user(email=TEST_EMAIL, name="A", password="short")
    assert caught.value.code == "password_too_short"


async def test_there_is_no_separate_sign_up_route(anon_client: AsyncClient) -> None:
    """There is one way in, and it is `/auth/signin`.

    With `open_signin` on, that route also provisions; with it off, accounts come from
    `auth.cli` or first-run bootstrap. Either way there is no second endpoint with its
    own rules to keep in step.
    """
    for path in ("/auth/signup", "/auth/register", "/users"):
        assert (await anon_client.post(path, json={})).status_code in (404, 405)


# ---------------------------------------------------------------------------
# Throttle unit behaviour
# ---------------------------------------------------------------------------


def test_the_throttle_forgets_failures_after_a_success() -> None:
    throttle = SignInThrottle(max_attempts=3, window_seconds=600, lockout_seconds=300)
    key = "a@b|1.2.3.4"
    throttle.record_failure(key)
    throttle.record_failure(key)
    throttle.record_success(key)
    # Someone who eventually remembers their password is not left one typo from a
    # lockout.
    throttle.record_failure(key)
    throttle.record_failure(key)
    assert throttle.check(key) == 0


def test_the_lockout_lapses() -> None:
    throttle = SignInThrottle(max_attempts=2, window_seconds=600, lockout_seconds=60)
    key = "a@b|1.2.3.4"
    throttle.record_failure(key, now=1000.0)
    throttle.record_failure(key, now=1001.0)
    assert throttle.check(key, now=1002.0) > 0
    assert throttle.check(key, now=1062.0) == 0


def test_the_rate_limiter_and_the_api_agree_on_the_cookie_name() -> None:
    """Two modules name the cookie; a rename that touches one is a silent bug."""
    from legal_lens.api.auth import COOKIE_NAME
    from legal_lens.middleware import SESSION_COOKIE

    assert COOKIE_NAME == SESSION_COOKIE


# ---------------------------------------------------------------------------
# Open sign-in (`LEGAL_LENS_OPEN_SIGNIN`)
# ---------------------------------------------------------------------------
# On by default: any email that signs in gets an account, created with the password
# submitted. What these pin down is the boundary — what it opens, and what it must
# still refuse.


async def test_an_unknown_email_gets_an_account_and_a_session(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    assert app.state.settings.open_signin is True

    response = await anon_client.post(
        "/auth/signin", json={"email": "newcomer@firm.test", "password": "a-brand-new-password"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "newcomer@firm.test"
    assert "HttpOnly" in response.headers["set-cookie"]
    # A usable session, not merely a 200.
    assert (await anon_client.get("/matters")).status_code == 200


async def test_the_password_from_the_first_visit_is_the_account_password(
    anon_client: AsyncClient,
) -> None:
    """The second visit is an ordinary sign-in, so a typo on the first is a new
    account rather than a lockout — and a *different* password later is refused."""
    await anon_client.post(
        "/auth/signin", json={"email": "newcomer@firm.test", "password": "a-brand-new-password"}
    )
    await anon_client.post("/auth/signout")

    wrong = await anon_client.post(
        "/auth/signin", json={"email": "newcomer@firm.test", "password": "some-other-password"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"

    right = await anon_client.post(
        "/auth/signin", json={"email": "newcomer@firm.test", "password": "a-brand-new-password"}
    )
    assert right.status_code == 200


async def test_open_sign_in_cannot_walk_into_an_existing_account(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """The line that matters. Provisioning happens only when there is no account; an
    address that already has one still needs that account's password."""
    await app.state.services.auth.create_user(
        email=TEST_EMAIL, name="Test Attorney", password=TEST_PASSWORD
    )
    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": "not-their-password"}
    )
    assert response.status_code == 401


async def test_open_sign_in_does_not_revive_a_disabled_account(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """Disabling must not be undone by signing in again, or it accomplishes nothing."""
    services = app.state.services
    user = await services.auth.create_user(email=TEST_EMAIL, name="T", password=TEST_PASSWORD)
    await services.db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))

    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 401
    assert await services.users.count() == 1


async def test_a_short_password_cannot_create_an_account(anon_client: AsyncClient) -> None:
    response = await anon_client.post(
        "/auth/signin", json={"email": "newcomer@firm.test", "password": "short"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "password_too_short"


async def test_accounts_made_this_way_are_still_isolated(
    app: FastAPI, anon_client: AsyncClient
) -> None:
    """Self-service accounts are ordinary accounts: separate matters, separate
    libraries. Open sign-in changes who may get in, not what they can see."""
    await anon_client.post(
        "/auth/signin", json={"email": "first@firm.test", "password": "a-long-password-one"}
    )
    made = await anon_client.post("/conversations", json={"title": "first user's work"})
    conversation_id = made.json()["id"]

    await anon_client.post(
        "/auth/signin", json={"email": "second@firm.test", "password": "a-long-password-two"}
    )
    assert (await anon_client.get("/conversations")).json() == []
    assert (await anon_client.get(f"/conversations/{conversation_id}/messages")).status_code == 404


async def test_health_says_whether_sign_in_is_open(anon_client: AsyncClient) -> None:
    """Answerable without reading the deployment's environment."""
    checks = (await anon_client.get("/health")).json()["checks"]
    assert checks["auth"] == "ok (open sign-in: any email gets an account)"


# ---------------------------------------------------------------------------
# Strict mode: the behaviour `open_signin=false` restores
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def strict_client(tmp_path) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    settings = Settings(
        environment="test", data_dir=tmp_path, llm_provider="echo", open_signin=False
    )
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        yield app, client


async def test_strict_mode_refuses_an_unknown_address(
    strict_client: tuple[FastAPI, AsyncClient],
) -> None:
    app, client = strict_client
    await app.state.services.auth.create_user(email=TEST_EMAIL, name="T", password=TEST_PASSWORD)

    unknown = await client.post(
        "/auth/signin", json={"email": "nobody@firm.test", "password": TEST_PASSWORD}
    )
    wrong = await client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": "wrong-password-entirely"}
    )

    # Indistinguishable, which is the property open sign-in gives up.
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["code"] == wrong.json()["code"] == "invalid_credentials"
    assert unknown.json()["detail"] == wrong.json()["detail"]
    assert await app.state.services.users.count() == 1


async def test_strict_mode_says_so_on_health(
    strict_client: tuple[FastAPI, AsyncClient],
) -> None:
    _, client = strict_client
    checks = (await client.get("/health")).json()["checks"]
    assert checks["auth"] == "ok (accounts created by an operator)"
