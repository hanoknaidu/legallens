"""The wire contract the React frontend already codes against.

These assertions are deliberately about the *shape* — camelCase keys, status codes,
timestamp format, problem bodies — rather than about behaviour that is also covered
elsewhere. The frontend was written against the MSW mocks, so anything here that
drifts breaks a client nobody will think to re-check.
"""

from __future__ import annotations

import re

from httpx import AsyncClient

# The frontend does `new Date(iso)`, which reads an offset-less stamp as *local* time.
# A naive timestamp therefore shifts every date in the UI by the viewer's offset.
UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

#: What the client sends for a conversation nobody has named yet. `title` is
#: required, so there is no such thing as an empty create body any more.
NEW = {"title": "New conversation"}


async def test_health_is_200_and_degraded_without_a_key(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200

    body = response.json()
    # `echo` needs no key, so the test settings are fully satisfied and report ok.
    assert body["status"] == "ok"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["llm_provider"].startswith("ok")
    assert set(body) == {"status", "version", "environment", "checks"}


async def test_default_matter_is_m_001(client: AsyncClient) -> None:
    # The frontend falls back to this literal when no matter is selected; a different
    # seed id silently breaks conversation creation.
    matters = (await client.get("/matters")).json()
    assert [m["id"] for m in matters] == ["m-001"]
    assert set(matters[0]) == {"id", "name", "client"}


async def test_conversation_round_trip_is_camel_case_with_utc_timestamps(
    client: AsyncClient,
) -> None:
    created = (await client.post("/conversations", json=NEW)).json()

    assert created["matterId"] == "m-001"
    # Echoed back from the request; the server no longer supplies a default.
    assert created["title"] == "New conversation"
    assert UTC_ISO.match(created["createdAt"]), created["createdAt"]
    assert UTC_ISO.match(created["updatedAt"]), created["updatedAt"]
    # snake_case must not leak through alongside the aliases.
    assert "matter_id" not in created
    assert "created_at" not in created


async def test_conversations_are_listed_newest_first(client: AsyncClient) -> None:
    for title in ("first", "second", "third"):
        await client.post("/conversations", json={"title": title})

    titles = [c["title"] for c in (await client.get("/conversations")).json()]
    assert titles == ["third", "second", "first"]


async def test_messages_distinguish_empty_from_missing(client: AsyncClient) -> None:
    conversation = (await client.post("/conversations", json=NEW)).json()

    # An existing conversation with no turns is an empty list. Collapsing this into a
    # 404 would make every newly created conversation look broken.
    empty = await client.get(f"/conversations/{conversation['id']}/messages")
    assert empty.status_code == 200
    assert empty.json() == []

    missing = await client.get("/conversations/c-nope/messages")
    assert missing.status_code == 404


async def test_rename_and_delete(client: AsyncClient) -> None:
    conversation = (await client.post("/conversations", json=NEW)).json()

    renamed = await client.patch(
        f"/conversations/{conversation['id']}", json={"title": "Termination rights"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Termination rights"

    assert (await client.delete(f"/conversations/{conversation['id']}")).status_code == 204
    # Idempotent-looking but not idempotent: the second delete is a 404, which is what
    # tells an optimistic UI that its row is already gone rather than that it failed.
    assert (await client.delete(f"/conversations/{conversation['id']}")).status_code == 404


async def test_failures_are_problem_json_carrying_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/conversations/c-nope/messages")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "conversation_not_found"
    # `detail` is what the UI shows a user, so it has to name the thing that failed.
    assert "c-nope" in body["detail"]
    assert body["requestId"] == response.headers["x-request-id"]


async def test_validation_errors_name_the_field(client: AsyncClient) -> None:
    conversation = (await client.post("/conversations", json=NEW)).json()

    response = await client.patch(f"/conversations/{conversation['id']}", json={"title": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["detail"].startswith("title:")


async def test_creating_against_an_unknown_matter_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/conversations", json={**NEW, "matterId": "m-nope"})
    assert response.status_code == 404
    assert response.json()["code"] == "matter_not_found"


async def test_requests_are_counted(client: AsyncClient) -> None:
    await client.get("/matters")
    snapshot = (await client.get("/metrics")).json()

    # Keyed by the route template, not the concrete path, so the series stays bounded.
    assert any("/api/matters" in key for key in snapshot["counters"])
    assert any("/api/matters" in key for key in snapshot["latency"])


async def test_the_anthropic_key_is_read_under_either_name(tmp_path, monkeypatch) -> None:
    """One key, two names, and it must work from `.env` as well as the environment.

    Every other setting takes the `LEGAL_LENS_` prefix, but the SDK, the `ant` CLI
    and the orchestrator all read `ANTHROPIC_API_KEY`, so requiring the same secret
    twice is how someone sets a key, sees no effect, and concludes the feature is
    broken. This shipped broken once: a validator reading `os.environ` looked right
    but never saw dotenv values, because those do not reach the process environment.
    """
    from legal_lens.config import Settings

    env = tmp_path / ".env"

    env.write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv\n")
    assert Settings(_env_file=env).anthropic_api_key == "sk-ant-from-dotenv"

    # The prefixed form wins, so a deployment can override an ambient key for this
    # service alone.
    env.write_text("ANTHROPIC_API_KEY=sk-ant-plain\nLEGAL_LENS_ANTHROPIC_API_KEY=sk-ant-prefixed\n")
    assert Settings(_env_file=env).anthropic_api_key == "sk-ant-prefixed"

    # And the environment still works with no dotenv file at all.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert Settings(_env_file=tmp_path / "absent").anthropic_api_key == "sk-ant-from-env"
