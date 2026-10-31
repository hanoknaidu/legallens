"""Strategy selection, the hashing embedder, and the pending-turn registry."""

from __future__ import annotations

import asyncio

import pytest

from legal_lens.chat.registry import PendingRegistry
from legal_lens.config import Settings
from legal_lens.retrieval import HashingEmbedder, MemoryVectorStore, select_pages
from legal_lens.retrieval.store import Indexed

PAGES = {
    "d-1": [
        (1, "MASTER SERVICES AGREEMENT between Northwind and Arbor Systems."),
        (2, "Section 7.2 Indemnification. The Supplier shall indemnify the Customer."),
        (3, "Section 9.1 Governing Law. Delaware law governs, venue in New Castle."),
    ]
}
DOCUMENTS = [("d-1", "MSA", 40)]


def _settings(**over) -> Settings:
    return Settings(environment="test", llm_provider="echo", **over)


# ---------------------------------------------------------------------------
# Strategy selection (BRD §4.2)
# ---------------------------------------------------------------------------


def test_a_small_corpus_goes_into_context_whole() -> None:
    selection = select_pages(
        question="What are the indemnification obligations?",
        documents=DOCUMENTS,
        pages_by_document=PAGES,
        settings=_settings(),
        embedder=HashingEmbedder(64),
        store=MemoryVectorStore(),
        owner_id="owner-local",
    )

    # Nothing can be missed, and questions about absence become answerable — which
    # retrieval cannot do, because "the contract is silent on X" is only provable if
    # the whole contract was read.
    assert selection.path == "whole_document"
    assert [p.page for p in selection.pages] == [1, 2, 3]


def test_an_oversized_corpus_switches_to_retrieval() -> None:
    store = MemoryVectorStore()
    embedder = HashingEmbedder(256)
    store.upsert(
        [
            Indexed(
                chunk_id=f"ch-{page}",
                document_id="d-1",
                matter_id="m-001",
                owner_id="owner-local",
                page_start=page,
                page_end=page,
                text=text,
                vector=embedder.embed(text),
            )
            for page, text in PAGES["d-1"]
        ]
    )

    selection = select_pages(
        question="indemnification",
        documents=[("d-1", "MSA", 10_000)],
        pages_by_document=PAGES,
        # A budget below the corpus size forces the RAG arm.
        settings=_settings(whole_document_token_budget=100, retrieval_top_n=2),
        embedder=embedder,
        store=store,
        owner_id="owner-local",
    )

    assert selection.path == "rag"
    assert selection.pages
    # The indemnification page must survive selection — missing it is the failure
    # that makes RAG worse than no retrieval.
    assert 2 in [p.page for p in selection.pages]


def test_retrieval_returns_nothing_when_no_documents_are_in_scope() -> None:
    selection = select_pages(
        question="anything",
        documents=[],
        pages_by_document={},
        settings=_settings(),
        embedder=HashingEmbedder(64),
        store=MemoryVectorStore(),
        owner_id="owner-local",
    )
    assert selection.pages == []
    assert selection.considered_documents == 0


def test_pages_keep_a_stable_order_so_citations_resolve() -> None:
    """Citation mapping depends on this.

    The Anthropic provider sends pages as a document's content blocks and resolves a
    citation's `start_block_index` back to a page by position. If selection returned
    pages in a different order on an identical request, the same citation would name
    a different page.
    """
    kwargs = dict(
        question="indemnification obligations",
        documents=DOCUMENTS,
        pages_by_document=PAGES,
        settings=_settings(),
        embedder=HashingEmbedder(64),
        store=MemoryVectorStore(),
        owner_id="owner-local",
    )
    first = select_pages(**kwargs)
    second = select_pages(**kwargs)

    assert [(p.document_id, p.page) for p in first.pages] == [
        (p.document_id, p.page) for p in second.pages
    ]


# ---------------------------------------------------------------------------
# Embedding and the store
# ---------------------------------------------------------------------------


def test_the_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder(128)
    text = "The Supplier shall indemnify the Customer."

    first, second = embedder.embed(text), embedder.embed(text)
    assert first == second, "an audit row must be reproducible"
    assert len(first) == 128
    assert abs(sum(v * v for v in first) ** 0.5 - 1.0) < 1e-9


def test_similar_text_scores_above_unrelated_text() -> None:
    embedder = HashingEmbedder(2048)
    query = embedder.embed("indemnify the customer against claims")

    def cosine(text: str) -> float:
        return sum(a * b for a, b in zip(query, embedder.embed(text), strict=False))

    related = cosine("The Supplier shall indemnify the Customer against all claims.")
    unrelated = cosine("Invoices are payable within thirty days of receipt.")
    assert related > unrelated


def test_an_empty_document_embeds_without_dividing_by_zero() -> None:
    assert HashingEmbedder(32).embed("   ") == [0.0] * 32


def test_the_store_scopes_by_owner() -> None:
    embedder = HashingEmbedder(64)
    store = MemoryVectorStore()
    store.upsert(
        [
            Indexed(
                chunk_id="ch-1",
                document_id="d-1",
                matter_id="m-001",
                owner_id="owner-local",
                page_start=1,
                page_end=1,
                text="indemnify the customer",
                vector=embedder.embed("indemnify the customer"),
            )
        ]
    )
    vector = embedder.embed("indemnify")

    assert store.search(vector, "owner-local", {"d-1"}, 10)
    # The Phase 2 regression test, at the retrieval layer.
    assert store.search(vector, "owner-someone-else", {"d-1"}, 10) == []


def test_removing_a_document_clears_its_vectors() -> None:
    embedder = HashingEmbedder(64)
    store = MemoryVectorStore()
    store.upsert(
        [
            Indexed(
                chunk_id="ch-1",
                document_id="d-1",
                matter_id="m-001",
                owner_id="owner-local",
                page_start=1,
                page_end=1,
                text="indemnify",
                vector=embedder.embed("indemnify"),
            )
        ]
    )
    assert store.count() == 1
    assert store.remove_document("d-1") == 1
    assert store.count() == 0


def test_an_unimplemented_embedding_provider_fails_loudly() -> None:
    from legal_lens.retrieval import embedder_for

    # Silently falling back would make a configured provider a lie, and the config
    # comment warns that a real vendor is a second processor of privileged text.
    with pytest.raises(NotImplementedError):
        embedder_for(_settings(embedding_provider="voyage"))


# ---------------------------------------------------------------------------
# Pending turns
# ---------------------------------------------------------------------------


def _register(registry: PendingRegistry, message_id: str, owner: str = "owner-local"):
    return registry.register(
        message_id=message_id,
        conversation_id="c-1",
        owner_id=owner,
        question="What are the indemnification obligations?",
        intent="ask",
        scoped_document_ids=None,
    )


async def test_a_turn_can_be_claimed_once() -> None:
    registry = PendingRegistry()
    _register(registry, "a-1")

    assert registry.claim("a-1", "owner-local") is not None
    # A second claim would race the first into a duplicate persisted message.
    assert registry.claim("a-1", "owner-local") is None


async def test_a_turn_is_invisible_to_another_owner() -> None:
    registry = PendingRegistry()
    _register(registry, "a-1")
    assert registry.claim("a-1", "owner-someone-else") is None


async def test_unclaimed_turns_expire() -> None:
    registry = PendingRegistry(ttl_seconds=0.01)
    _register(registry, "a-1")

    await asyncio.sleep(0.05)
    # Otherwise a client that posts and never opens the stream leaks an entry per
    # message for the life of the process.
    assert registry.claim("a-1", "owner-local") is None


async def test_a_claimed_turn_is_not_expired_mid_generation() -> None:
    registry = PendingRegistry(ttl_seconds=0.01)
    _register(registry, "a-1")
    turn = registry.claim("a-1", "owner-local")
    assert turn is not None

    await asyncio.sleep(0.05)
    _register(registry, "a-2")  # triggers the sweep

    # A long answer must not have its entry pulled out from under it.
    assert len(registry) >= 1
    assert not turn.cancel.is_set()


async def test_stop_with_no_id_cancels_everything_this_owner_has_in_flight() -> None:
    registry = PendingRegistry()
    first = _register(registry, "a-1")
    second = _register(registry, "a-2")
    other = _register(registry, "a-3", owner="owner-someone-else")

    # Phase 1 is single-user and the stop button posts an empty body, so this is the
    # form that actually runs.
    assert registry.cancel("owner-local") == 2
    assert first.cancel.is_set() and second.cancel.is_set()
    assert not other.cancel.is_set()


async def test_stop_can_target_one_turn() -> None:
    registry = PendingRegistry()
    first = _register(registry, "a-1")
    second = _register(registry, "a-2")

    assert registry.cancel("owner-local", "a-2") == 1
    assert second.cancel.is_set()
    assert not first.cancel.is_set()
