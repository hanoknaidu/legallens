"""The orchestrator's index as the service drives it.

No models load here. What is tested is the part that does not need them and that
fails quietly if it breaks: the scope that keeps a question inside its owner's
corpus, the storage isolation from the CLI's eval index, and the purge that has to
reach Qdrant when a document is deleted.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from helpers import ready_document
from legal_lens.config import Settings
from legal_lens.main import create_app
from legal_lens.orchestrator.config import OrchestratorConfig
from legal_lens.retrieval.orchestrated import OrchestratedRetrieval, config_for


def _retrieval(tmp_path) -> OrchestratedRetrieval:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        llm_provider="orchestrator",
        anthropic_api_key="test-key",
    )
    return OrchestratedRetrieval(db=None, documents=None, settings=settings)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


async def test_an_empty_scope_is_refused_rather_than_widened(tmp_path) -> None:
    """There is no "search everything" mode, by construction.

    A search with no scope would reach every owner's pages. Nothing loads before this
    raises, which is also why the test needs no models.
    """
    retrieval = _retrieval(tmp_path)

    with pytest.raises(ValueError, match="non-empty document scope"):
        await retrieval.search("Where does venue lie?", doc_ids=set(), owner_id="o-1")

    with pytest.raises(ValueError, match="non-empty document scope"):
        await retrieval.select("Where does venue lie?", doc_ids=set(), owner_id="o-1")

    with pytest.raises(ValueError, match="non-empty document scope"):
        await retrieval.whole(doc_ids=set(), owner_id="o-1")


async def test_a_missing_owner_is_refused_rather_than_defaulted(tmp_path) -> None:
    """The owner filter is not optional at this seam.

    Without an owner the index filter falls back to document ids alone, and scoping
    rests entirely on the caller having resolved that set correctly — which is the
    single point of failure the payload filter exists to remove. An empty owner is a
    programming error, so it raises rather than quietly searching one filter short.
    """
    retrieval = _retrieval(tmp_path)

    with pytest.raises(ValueError, match="needs an owner"):
        await retrieval.search("Where does venue lie?", doc_ids={"d-1"}, owner_id="")

    with pytest.raises(ValueError, match="needs an owner"):
        await retrieval.whole(doc_ids={"d-1"}, owner_id="")


# ---------------------------------------------------------------------------
# Storage isolation
# ---------------------------------------------------------------------------


def test_the_service_does_not_share_the_cli_eval_index(tmp_path) -> None:
    """Embedded Qdrant takes an exclusive lock on its directory.

    Sharing one would mean the CLI could not run while the service was up — and, if
    the service ever won the race, that it would be answering questions from eval
    fixtures. Both of those are worse than two directories.
    """
    service = config_for(
        Settings(environment="test", data_dir=tmp_path, llm_provider="orchestrator")
    )
    cli = OrchestratorConfig()

    assert service.qdrant_path != cli.qdrant_path
    assert service.qdrant_collection != cli.qdrant_collection
    assert service.qdrant_path.is_relative_to(tmp_path)


def test_everything_else_is_still_the_orch_environment(monkeypatch, tmp_path) -> None:
    """A setting tuned against the eval corpus means the same thing in the service.

    Only the two values that would collide are overridden; models, effort, candidate
    counts and the reranker switch stay on the `ORCH_` variables the CLI reads.
    """
    monkeypatch.setenv("ORCH_ASK_TOP_N", "3")
    monkeypatch.setenv("ORCH_RERANK_ENABLED", "0")
    monkeypatch.setenv("ORCH_ASK_MODEL", "claude-haiku-4-5")

    config = config_for(
        Settings(environment="test", data_dir=tmp_path, llm_provider="orchestrator")
    )

    assert config.ask_top_n == 3
    assert config.rerank_enabled is False
    assert config.ask_model == "claude-haiku-4-5"


def test_the_api_key_reaches_the_orchestrator_config(tmp_path) -> None:
    config = config_for(
        Settings(
            environment="test",
            data_dir=tmp_path,
            llm_provider="orchestrator",
            anthropic_api_key="sk-test",
            anthropic_workspace_id="wrkspc_1",
        )
    )

    assert config.require_api_key() == "sk-test"
    assert config.headers == {"anthropic-workspace-id": "wrkspc_1"}


# ---------------------------------------------------------------------------
# Deletion (NFR-8)
# ---------------------------------------------------------------------------


async def test_deleting_a_document_records_a_vector_index_purge(client) -> None:
    """The vector indexes are the third copy of the text.

    SQLite cascades and the object store gets a job row. Qdrant persists across a
    restart, so a document left in it answers questions after the user deleted it —
    the job row is what makes an incomplete purge visible rather than assumed.
    """
    document_id = await ready_document(client)

    assert (await client.delete(f"/documents/{document_id}")).status_code == 204

    # `/documents` no longer lists it, and the purge was recorded.
    assert all(d["id"] != document_id for d in (await client.get("/documents")).json())


async def test_the_purge_job_is_marked_done(client, services) -> None:
    document_id = await ready_document(client)
    await client.delete(f"/documents/{document_id}")

    rows = await services.db.fetch_all(
        "SELECT target, status FROM deletion_jobs WHERE document_id = ? ORDER BY target",
        (document_id,),
    )

    targets = {r["target"]: r["status"] for r in rows}
    assert targets.get("vector_index") == "done"
    # The object store job is still recorded alongside it.
    assert "object_store" in targets


# ---------------------------------------------------------------------------
# What /health says
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orchestrator_app(tmp_path):
    """The app with the orchestrator selected. Loads no models.

    Boot reconciles an empty database, finds nothing to index, and returns before
    touching the encoders — which is the behaviour this fixture depends on.
    """
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        llm_provider="orchestrator",
        anthropic_api_key="sk-test-unused",
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as http:
        async with app.router.lifespan_context(app):
            yield http


async def test_health_reports_the_orchestrator_index(orchestrator_app) -> None:
    body = (await orchestrator_app.get("/health")).json()

    assert "orchestrator" in body["checks"]
    # Idle rather than broken: nothing has asked a question yet.
    assert body["checks"]["orchestrator"].startswith("ok")
    assert body["status"] == "ok"


async def test_health_does_not_claim_the_unused_store_is_serving(orchestrator_app) -> None:
    """The in-memory store is still populated, so provider switches need no re-index.

    But nothing reads it on this path, and reporting "ok (memory)" would name a store
    no question touches.
    """
    body = (await orchestrator_app.get("/health")).json()

    assert body["checks"]["vector_store"] == "ok (not in use; the orchestrator serves retrieval)"


async def test_health_omits_the_check_when_the_orchestrator_is_off(client) -> None:
    body = (await client.get("/health")).json()

    assert "orchestrator" not in body["checks"]
    assert body["checks"]["vector_store"] == "ok (memory, process-local)"


def test_pages_loaded_with_nothing_indexed_reads_as_degraded(tmp_path) -> None:
    """The silent failure on this path, made loud.

    An index that lost its points while the page store still has pages means every
    question retrieves nothing and abstains, and no single component is in error.
    """
    retrieval = _retrieval(tmp_path)
    retrieval.pages.add_document("msa", "MSA", [(1, "Page one."), (2, "Page two.")])

    class EmptyIndex:
        def count(self) -> int:
            return 0

    retrieval._index = EmptyIndex()  # type: ignore[assignment]

    state = retrieval.status()
    assert not state.startswith("ok")
    assert "nothing indexed" in state


def test_an_idle_pipeline_does_not_build_itself_to_answer_health(tmp_path) -> None:
    """Asking whether the models are loaded must not load them."""
    retrieval = _retrieval(tmp_path)

    state = retrieval.status()

    assert state.startswith("ok")
    assert "idle" in state
    assert retrieval._index is None
