from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from legal_lens.config import PHASE1_OWNER_ID, Settings
from legal_lens.db.session import Database
from legal_lens.main import create_app
from legal_lens.services import Services


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # A real file rather than :memory:, because the connection is opened once at
    # lifespan and an in-memory database would not survive the way the app uses it.
    return Settings(environment="test", data_dir=tmp_path, llm_provider="echo")


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """The app over ASGI, exercised through its real lifespan.

    Tests go through HTTP rather than calling repositories directly: the contract the
    frontend depends on includes status codes, aliases and error bodies, and none of
    those are visible from inside a function call.
    """
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as c:
        async with app.router.lifespan_context(app):
            yield c


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
