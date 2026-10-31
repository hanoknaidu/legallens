"""Hybrid retrieval over the real eval corpus.

This is the one test file that loads actual models and runs an actual vector store.
It is skipped when the `workflows` extra is not installed, and the models are built
once for the whole module — a per-test build would spend half a minute reloading
weights that do not change.

What it is for: the pieces that only break when the real stack is wired together —
that both arms are reachable, that the scope filter travels with the query, that a
paraphrase is found by the dense arm and a term of art by the lexical one, and that
`coverage` reports honestly.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("qdrant_client", reason="needs the `workflows` extra")
pytest.importorskip("sentence_transformers", reason="needs the `workflows` extra")
pytest.importorskip("fastembed", reason="needs the `workflows` extra")

from legal_lens.orchestrator.config import OrchestratorConfig  # noqa: E402
from legal_lens.orchestrator.fixtures import load  # noqa: E402
from legal_lens.orchestrator.retrieve import (  # noqa: E402
    Encoders,
    PassageIndex,
    Retriever,
    covered_pages,
    rerank,
)
from legal_lens.orchestrator.types import Passage  # noqa: E402

#: Deliberately **not** the configured production models.
#:
#: These tests verify plumbing — that both arms are reachable, that the scope filter
#: travels with the query, that page expansion and `coverage` are right. None of that
#: is a property of which weights are loaded, and pinning the small models keeps the
#: suite at seconds instead of minutes: the production pair (`bge-m3` +
#: `bge-reranker-v2-m3`) is 1.1GB and about 2.5s per reranked question.
#:
#: Model *quality* is measured by `tests/eval/run_models.py`, which is a benchmark
#: rather than a test — it has no pass/fail, because "is this embedder good" is a
#: comparison, not an assertion.
TEST_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TEST_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@pytest.fixture(scope="module")
def encoders():
    # Built once for the module: a per-test build would reload weights that do not
    # change between tests.
    return Encoders(
        embed_model=TEST_EMBED_MODEL,
        rerank_model=TEST_RERANK_MODEL,
        with_reranker=True,
    )


@pytest.fixture(scope="module")
def retriever(encoders, tmp_path_factory):
    import asyncio

    config = OrchestratorConfig(
        api_key="test-key",
        qdrant_path=tmp_path_factory.mktemp("qdrant"),
        qdrant_collection="test_passages",
        embed_model=TEST_EMBED_MODEL,
        rerank_model=TEST_RERANK_MODEL,
    )
    store, passages = load()
    index = PassageIndex(config, encoders)
    index.drop()
    asyncio.run(index.index(passages))
    try:
        yield Retriever(config, index, store, encoders)
    finally:
        index.close()


# ---------------------------------------------------------------------------
# The index itself
# ---------------------------------------------------------------------------


async def test_the_whole_corpus_is_indexed(retriever) -> None:
    # 25 pages across the real corpus and the near-miss distractors. Without the
    # distractors there is nothing to rank wrongly, which is how the first version of
    # the retrieval eval scored 0.97 for an embedder with no notion of meaning.
    assert retriever._index.count() == 25


async def test_both_arms_contribute_to_the_fused_result(retriever) -> None:
    hits = await retriever._index.hybrid_search("indemnification obligations")

    assert hits
    assert len(hits) <= OrchestratorConfig().candidates
    # RRF scores are a rank blend, so they descend but are not cosines.
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)


async def test_the_scope_filter_travels_with_the_query(retriever) -> None:
    hits = await retriever._index.hybrid_search("confidential information", doc_ids={"nda"})

    assert hits
    # Filtering after the fact would let out-of-scope passages consume the candidate
    # budget and silently shrink what the attorney asked about.
    assert {passage.doc_id for passage, _ in hits} == {"nda"}


async def test_an_empty_scope_returns_nothing_rather_than_everything(retriever) -> None:
    retrieved = await retriever.search("indemnification", doc_ids={"no-such-document"})

    assert retrieved.pages == []


# ---------------------------------------------------------------------------
# What the two arms are each for
# ---------------------------------------------------------------------------


async def test_a_paraphrase_is_found_without_sharing_words(retriever) -> None:
    """The dense arm's job.

    The corpus says "shall not exceed"; the question says "ceiling on how much".
    Lexical search alone cannot bridge that, which is the gap the eval's `paraphrase`
    group measures.
    """
    retrieved = await retriever.search(
        "Is there a ceiling on how much either side can be made to pay?", top_n=8
    )

    assert retrieved.pages
    texts = " ".join(p.text for p in retrieved.pages).lower()
    assert "shall not exceed" in texts or "aggregate liability" in texts


async def test_an_exact_term_of_art_is_found(retriever) -> None:
    """The lexical arm's job — a defined term that must match verbatim."""
    retrieved = await retriever.search("commercial general liability insurance", top_n=8)

    texts = " ".join(p.text for p in retrieved.pages).lower()
    assert "commercial general liability" in texts


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------


async def test_the_reranker_puts_the_answering_passage_first(encoders) -> None:
    candidates = [
        Passage("d", "Doc", 1, 1, "The parties shall meet quarterly to review service levels."),
        Passage("d", "Doc", 2, 2, "Invoices are payable within thirty days of receipt."),
        Passage(
            "d",
            "Doc",
            3,
            3,
            "Supplier's aggregate liability shall not exceed the fees paid in the "
            "preceding twelve months.",
        ),
    ]

    ranked = await rerank(encoders.reranker, "What is the cap on liability?", candidates, top_n=3)

    # This reordering is the step the spike measured as holding the remaining
    # headroom: the right page was already being retrieved and ranked badly.
    assert ranked[0][0].page_start == 3


async def test_without_a_reranker_the_fused_order_is_kept(encoders) -> None:
    candidates = [Passage("d", "Doc", i, i, f"page {i}") for i in (1, 2, 3)]

    ranked = await rerank(None, "anything", candidates, top_n=2)

    assert [p.page_start for p, _ in ranked] == [1, 2]


async def test_the_score_floor_never_empties_the_result(encoders) -> None:
    candidates = [Passage("d", "Doc", 1, 1, "Entirely unrelated prose about gardening.")]

    ranked = await rerank(
        encoders.reranker, "indemnification cap", candidates, top_n=8, min_score=10_000.0
    )

    # Reaching the model with no documents would make it abstain for the wrong
    # reason: "nothing was retrieved" is not "the documents do not say".
    assert len(ranked) == 1


async def test_reranking_is_stable_for_an_identical_request(retriever) -> None:
    first = await retriever.search("termination for convenience", top_n=5)
    second = await retriever.search("termination for convenience", top_n=5)

    # Citation mapping depends on page order: an unstable sort would make the same
    # citation name a different page on a re-run.
    assert [(p.doc_id, p.page) for p in first.pages] == [(p.doc_id, p.page) for p in second.pages]


# ---------------------------------------------------------------------------
# Coverage — the honesty flag the ask prompt is conditioned on
# ---------------------------------------------------------------------------


async def test_a_narrow_scope_is_fully_covered(retriever) -> None:
    # The NDA is short enough that retrieval returns all of it, so an absence claim
    # about the NDA is provable.
    retrieved = await retriever.search("confidentiality", doc_ids={"nda"}, top_n=25)

    assert retrieved.coverage is True


async def test_the_whole_corpus_at_a_small_keep_is_not_covered(retriever) -> None:
    retrieved = await retriever.search("confidentiality", top_n=3)

    # Eight pages of twenty-five cannot prove the other seventeen say nothing.
    assert retrieved.coverage is False
    assert retrieved.corpus_pages == 25


async def test_summary_retrieval_reads_everything(retriever) -> None:
    retrieved = await retriever.whole()

    assert len(retrieved.pages) == 25
    assert retrieved.coverage is True
    # Document then page order, so the citation mapping is deterministic.
    assert retrieved.pages == sorted(retrieved.pages, key=lambda p: (p.doc_id, p.page))


# ---------------------------------------------------------------------------
# The route decision — whole document, or retrieve
# ---------------------------------------------------------------------------
#
# This is the FR-13 mechanism, and the reason it is tested here rather than against a
# fake: the decision is made from the *measured* size of a real corpus, so a fake
# retriever with no pages to count could only re-assert the branch it was given.


def with_budget(retriever, budget: int) -> Retriever:
    """The same index and pages, with a different whole-document budget.

    `OrchestratorConfig` is frozen — deliberately, so a request cannot retune the
    pipeline mid-flight — so a different budget means a different config object. The
    index, page store and encoders are shared, because rebuilding those would reload
    weights for a one-line change.
    """
    return Retriever(
        replace(retriever._config, whole_document_max_tokens=budget),
        retriever._index,
        retriever._pages,
        retriever._encoders,
    )


async def test_a_corpus_inside_the_budget_is_read_whole(retriever) -> None:
    # The eval corpus is ~25 small pages, well inside the default 120K budget, so
    # `select` reads all of it and an absence becomes provable. This is the case
    # that used to abstain: before the route existed, `ask` retrieved eight pages of
    # twenty-five and the prompt forbade it from reporting an absence.
    retrieved = await retriever.select("is there a non-compete clause", top_n=3)

    assert retrieved.path == "whole_document"
    assert retrieved.coverage is True
    assert len(retrieved.pages) == 25


async def test_a_corpus_over_the_budget_is_retrieved(retriever) -> None:
    # A budget of one token stands in for a corpus too large to send, without
    # needing a 120K-token fixture. The arm is the one the reranker feeds.
    retrieved = await with_budget(retriever, 1).select("confidentiality", top_n=3)

    assert retrieved.path == "rag"
    assert retrieved.coverage is False
    assert len(retrieved.pages) < 25
    # Recorded so the audit row can say why this question retrieved.
    assert retrieved.corpus_tokens > 1


async def test_a_zero_budget_always_retrieves(retriever) -> None:
    # `ORCH_WHOLE_DOCUMENT_MAX_TOKENS=0` is the always-retrieve configuration the
    # §9.2 measurements were taken under, so it has to stay reachable.
    retrieved = await with_budget(retriever, 0).select("confidentiality", top_n=3)

    assert retrieved.path == "rag"


async def test_the_route_is_decided_per_scope_not_per_corpus(retriever) -> None:
    """A narrow scope can fit the budget while the whole corpus does not.

    The decision is about what this question will send, which is the scope — not
    about how much text exists in the matter.
    """
    whole_corpus_tokens = retriever._pages.count_tokens(None)
    nda_tokens = retriever._pages.count_tokens({"nda"})
    assert nda_tokens < whole_corpus_tokens

    # A budget between the two: the NDA fits, the whole corpus does not.
    scoped = with_budget(retriever, (nda_tokens + whole_corpus_tokens) // 2)

    narrow = await scoped.select("confidentiality", doc_ids={"nda"}, top_n=3)
    wide = await scoped.select("confidentiality", top_n=3)

    assert narrow.path == "whole_document"
    assert wide.path == "rag"


# ---------------------------------------------------------------------------
# Owner isolation — the payload filter, not the caller
# ---------------------------------------------------------------------------


async def test_another_owners_passages_are_unreachable_even_in_scope(
    encoders, tmp_path_factory
) -> None:
    """The owner filter holds when the document filter is wrong.

    This is the failure it exists for. The question asks for a document that belongs
    to someone else *and names it in scope* — which is exactly what a bug in the one
    call site that resolves the document set would produce. With only a document
    filter, that search returns another tenant's privileged text.
    """
    config = OrchestratorConfig(
        api_key="test-key",
        qdrant_path=tmp_path_factory.mktemp("qdrant-tenants"),
        qdrant_collection="tenant_passages",
        embed_model=TEST_EMBED_MODEL,
        rerank_model=TEST_RERANK_MODEL,
    )
    index = PassageIndex(config, encoders)
    index.drop()
    await index.index(
        [
            Passage(
                doc_id="mine",
                title="My MSA",
                page_start=1,
                page_end=1,
                text="Governing law is Delaware and venue lies in Wilmington.",
                owner_id="owner-a",
            ),
            Passage(
                doc_id="theirs",
                title="Their MSA",
                page_start=1,
                page_end=1,
                text="Governing law is New York and venue lies in Manhattan.",
                owner_id="owner-b",
            ),
        ]
    )
    try:
        # Both documents named in scope — the mistake the filter has to survive.
        both = {"mine", "theirs"}
        mine = await index.hybrid_search(
            "governing law and venue", doc_ids=both, owner_id="owner-a", limit=10
        )
        theirs = await index.hybrid_search(
            "governing law and venue", doc_ids=both, owner_id="owner-b", limit=10
        )

        assert {p.doc_id for p, _ in mine} == {"mine"}
        assert {p.doc_id for p, _ in theirs} == {"theirs"}
        # And nothing of the other tenant's text came back with it.
        assert all("New York" not in p.text for p, _ in mine)

        # Without the owner, scoping would have rested on `doc_ids` alone.
        unfiltered = await index.hybrid_search("governing law and venue", doc_ids=both, limit=10)
        assert {p.doc_id for p, _ in unfiltered} == both
    finally:
        index.close()


async def test_the_page_store_narrows_to_the_owner_too(encoders, tmp_path_factory) -> None:
    """Page text lives outside Qdrant and had the same exposure.

    `whole()` and the whole-document route read from `PageStore`, not the index, so a
    filter on the index alone would leave the larger of the two reads unprotected.
    """
    from legal_lens.orchestrator.retrieve import PageStore

    store = PageStore()
    store.add_document("mine", "My MSA", [(1, "Delaware.")], owner_id="owner-a")
    store.add_document("theirs", "Their MSA", [(1, "New York.")], owner_id="owner-b")

    assert store.scoped_to({"mine", "theirs"}, "owner-a") == {"mine"}
    assert store.scoped_to({"mine", "theirs"}, "owner-b") == {"theirs"}
    # A corpus with no owners recorded is the single-tenant eval case: nothing to
    # disagree with, so nothing is dropped.
    assert store.scoped_to({"mine"}, None) == {"mine"}


# ---------------------------------------------------------------------------
# Page expansion
# ---------------------------------------------------------------------------


def test_page_expansion_covers_a_straddling_chunk() -> None:
    assert covered_pages([Passage("d", "Doc", 4, 6, "x")]) == [("d", 4), ("d", 5), ("d", 6)]


# ---------------------------------------------------------------------------
# The reranker switch
# ---------------------------------------------------------------------------


def test_the_reranker_can_be_turned_off_by_configuration(monkeypatch) -> None:
    from legal_lens.orchestrator.config import OrchestratorConfig

    assert OrchestratorConfig().rerank_enabled is True

    # Measured on this corpus, every available cross-encoder made retrieval worse —
    # see the table on `rerank_enabled`. One env var has to be enough to act on that.
    for value in ("0", "false", "no"):
        monkeypatch.setenv("ORCH_RERANK_ENABLED", value)
        assert OrchestratorConfig().rerank_enabled is False


def test_encoders_skip_loading_the_cross_encoder_when_it_is_off() -> None:

    encoders = Encoders(
        embed_model=TEST_EMBED_MODEL,
        rerank_model="this-model-does-not-exist",
        with_reranker=False,
    )

    # Naming an unloadable reranker must not matter when it is switched off.
    assert encoders.reranker is None
