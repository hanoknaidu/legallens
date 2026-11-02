from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from legal_lens.config import PHASE1_OWNER_ID, Settings
from legal_lens.db.session import Database
from legal_lens.main import create_app
from legal_lens.services import Services

#: The account every authenticated test signs in as. A real sign-in through the real
#: route, rather than a dependency override: an override would mean the suite never
#: exercises the cookie, the session row or the 401, which is most of what
#: authentication *is*. It costs one scrypt verify per test, and `environment="test"`
#: lowers the cost parameter so that is ~5ms rather than ~90ms (config.scrypt_n).
TEST_EMAIL = "attorney@firm.test"
TEST_PASSWORD = "correct-horse-battery-staple"
TEST_NAME = "Test Attorney"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # A real file rather than :memory:, because the connection is opened once at
    # lifespan and an in-memory database would not survive the way the app uses it.
    # `require_auth=True` explicitly: the shipped default is a showcase build with no
    # enforcement, and a suite that inherited that would assert nothing about the 401s
    # it is here to check. The demo path has its own tests, which opt back out.
    return Settings(environment="test", data_dir=tmp_path, llm_provider="echo", require_auth=True)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """The application object, so a test can reach `app.state.services` without
    digging it back out of the client's private transport."""
    return create_app(settings)


@pytest_asyncio.fixture
async def anon_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """The app with nobody signed in.

    What the tests about authentication itself use, and what proves a protected route
    answers 401. Every other test wants `client`.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest_asyncio.fixture
async def client(app: FastAPI, anon_client: AsyncClient) -> AsyncClient:
    """The app over ASGI, signed in, exercised through its real lifespan.

    Tests go through HTTP rather than calling repositories directly: the contract the
    frontend depends on includes status codes, aliases and error bodies, and none of
    those are visible from inside a function call.

    The account is created through the service and then signed in through
    `POST /auth/signin`, so the cookie httpx holds from here on is one a browser would
    have been given. Because it is the first account, its id is `PHASE1_OWNER_ID` —
    which is what the fixtures that write rows directly also use, so the two agree.
    """
    services: Services = app.state.services
    await services.auth.create_user(email=TEST_EMAIL, name=TEST_NAME, password=TEST_PASSWORD)

    response = await anon_client.post(
        "/auth/signin", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return anon_client


@pytest_asyncio.fixture
async def services(settings: Settings) -> AsyncIterator[Services]:
    db = Database(settings.db_path)
    await db.connect()
    container = Services(db, settings)
    await container.bootstrap(PHASE1_OWNER_ID)
    try:
        yield container
    finally:
        await db.close()
