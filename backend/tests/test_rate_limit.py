"""Rate limiting, and the paths that must never be limited.

`rate_limit_per_minute` sat in the configuration unenforced for the whole project,
which is worse than absent: the setting reads as a control that exists. These tests
are mostly about the exemptions, because those are what make a limiter safe to turn
on — a limiter that throttles the health endpoint or a long answer causes the outage
it was meant to prevent.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from legal_lens.config import Settings
from legal_lens.main import create_app
from legal_lens.middleware import RateLimiter

# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_requests_are_allowed_up_to_the_limit_then_refused() -> None:
    limiter = RateLimiter(3)

    for _ in range(3):
        allowed, _ = limiter.check("owner", now=100.0)
        assert allowed
    allowed, retry_after = limiter.check("owner", now=100.0)
    assert not allowed
    # The client is told when to come back, not just told no.
    assert retry_after > 0


def test_the_window_slides_rather_than_resetting() -> None:
    limiter = RateLimiter(2)
    limiter.check("owner", now=0.0)
    limiter.check("owner", now=30.0)

    assert limiter.check("owner", now=31.0)[0] is False
    # The first request ages out 60s after *it* happened, not at a fixed boundary.
    # With fixed windows a client can send double the allowance across the boundary.
    assert limiter.check("owner", now=61.0)[0] is True
    assert limiter.check("owner", now=61.5)[0] is False
    assert limiter.check("owner", now=91.0)[0] is True


def test_owners_have_separate_allowances() -> None:
    limiter = RateLimiter(1)
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("a", now=0.0)[0] is False
    # Phase 1 has one owner, so this only matters once authentication lands — which
    # is exactly why it is keyed by owner now rather than retrofitted later.
    assert limiter.check("b", now=0.0)[0] is True


def test_a_limit_of_zero_disables_limiting() -> None:
    limiter = RateLimiter(0)
    for _ in range(50):
        assert limiter.check("owner", now=0.0)[0] is True


def test_remaining_counts_down() -> None:
    limiter = RateLimiter(3)
    assert limiter.remaining("owner") == 3
    limiter.check("owner", now=0.0)
    assert limiter.remaining("owner") == 2


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def tight_settings(tmp_path) -> Settings:
    return Settings(
        environment="test", data_dir=tmp_path, llm_provider="echo", rate_limit_per_minute=3
    )


async def test_exceeding_the_limit_returns_problem_json_with_retry_after(
    tight_settings: Settings,
) -> None:
    app = create_app(tight_settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        for _ in range(3):
            assert (await client.get("/matters")).status_code == 200

        refused = await client.get("/matters")
        assert refused.status_code == 429
        assert refused.headers["content-type"].startswith("application/problem+json")
        assert int(refused.headers["Retry-After"]) >= 1

        body = refused.json()
        assert body["code"] == "rate_limited"
        # The detail is written for a person: it names the limit and when to retry.
        assert "3 requests in a minute" in body["detail"]
        assert body["requestId"]


async def test_the_limit_is_advertised_on_every_response(tight_settings: Settings) -> None:
    app = create_app(tight_settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        first = await client.get("/matters")
        assert first.headers["X-RateLimit-Limit"] == "3"
        # So a client can back off before being refused rather than after.
        assert first.headers["X-RateLimit-Remaining"] == "2"


async def test_health_and_metrics_are_never_limited(tight_settings: Settings) -> None:
    app = create_app(tight_settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        # A monitor that gets 429'd reports an outage that is not happening, and
        # /health is the one endpoint that must answer when the service is struggling.
        for _ in range(10):
            assert (await client.get("/health")).status_code == 200
        for _ in range(10):
            assert (await client.get("/metrics")).status_code == 200


async def test_the_answer_stream_is_not_limited(tight_settings: Settings) -> None:
    """A streamed answer holds one request open for minutes.

    Counting it against a per-minute allowance would make a slow answer look like
    abuse, and the 429 would arrive mid-conversation for a user who sent one message.
    """
    app = create_app(tight_settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        # Spend the whole allowance first.
        for _ in range(3):
            await client.get("/matters")
        assert (await client.get("/matters")).status_code == 429

        # The stream endpoint still answers — a 404 here is the pending-turn check,
        # not the limiter, which is the point: it was reached at all.
        streamed = await client.get("/chat/stream?messageId=a-nope")
        assert streamed.status_code == 404


async def test_the_default_limit_does_not_interfere_with_normal_use(tmp_path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, llm_provider="echo")
    assert settings.rate_limit_per_minute == 60

    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        # A user clicking around sends nothing like 60 requests a minute; the suite
        # itself would fail noisily if the default were too tight.
        for _ in range(20):
            assert (await client.get("/matters")).status_code == 200
