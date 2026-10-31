"""Measure retrieval quality and cost on real hardware, before anything is built on it.

Scores configurations over the same corpus and questions:

1. `hashing`      — what runs today: a hashed bag of words with no notion of meaning.
2. `dense`        — a real embedding model.
3. `dense+rerank` — the same, with a cross-encoder over the fused candidates.

**Why more than one metric.** The first version of this harness reported page recall@8
over a 14-page corpus and gave the hashing embedder 0.97 — not because that embedder
is good, but because keeping 8 of 14 pages puts 57% of the corpus in front of the
model regardless of ranking. Membership at a generous k cannot discriminate. So:

- `recall@1`  — did the best-ranked page actually answer the question? The sharpest
                signal, and the one that tracks answer quality.
- `recall@3`  — survives into a tight context.
- `recall@8`  — survives into the prompt at the configured `retrieval_top_n`.
- `MRR`       — how far down the list the first correct page sits. Sensitive to
                ranking rather than to membership.

Distractor documents are pooled in for the same reason: without near-miss contracts
there is nothing to rank wrongly.

`absence` questions are excluded — they have no correct page, and they score the
generation step's honesty rather than retrieval.

Run: `uv run python tests/eval/run_spike.py`
Env: `SPIKE_EMBED`, `SPIKE_RERANK`, `SPIKE_DEVICE`, `SPIKE_SKIP_RERANK=1`
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from corpus import DOCUMENTS  # noqa: E402
from distractors import DISTRACTORS  # noqa: E402
from filler import FILLER  # noqa: E402
from questions import QUESTIONS  # noqa: E402

from legal_lens.lib_text import score_text, search_terms  # noqa: E402
from legal_lens.retrieval.embeddings import HashingEmbedder  # noqa: E402

EMBED_MODEL = os.environ.get("SPIKE_EMBED", "Qwen/Qwen3-Embedding-0.6B")
RERANK_MODEL = os.environ.get("SPIKE_RERANK", "BAAI/bge-reranker-v2-m3")
DEVICE = os.environ.get("SPIKE_DEVICE", "mps")
SKIP_RERANK = os.environ.get("SPIKE_SKIP_RERANK") == "1"

CANDIDATES = 25
KEEP = 8
CUTOFFS = (1, 3, 8)

#: Whether the boilerplate pages are pooled in. On by default, because without them
#: `CANDIDATES` equals the corpus and fusion hands the reranker everything — the
#: regime `workflows-plan.md` §9.2 took its reranker numbers in, and the reason those
#: numbers could not be trusted. `SPIKE_NO_FILLER=1` reproduces that smaller corpus,
#: which is the only way to compare against the recorded figures.
WITH_FILLER = os.environ.get("SPIKE_NO_FILLER") != "1"


@dataclass(frozen=True)
class Page:
    key: str
    page: int
    text: str
    distractor: bool

    @property
    def ref(self) -> tuple[str, int]:
        return (self.key, self.page)


#: Every page the ranker sees. `distractor=True` marks a page that answers nothing,
#: which is both the near-miss contracts and the boilerplate.
PAGES: list[Page] = [
    *(Page(d.key, i + 1, p, False) for d in DOCUMENTS for i, p in enumerate(d.pages)),
    *(Page(d.key, i + 1, p, True) for d in DISTRACTORS for i, p in enumerate(d.pages)),
    *(
        Page(d.key, i + 1, p, True)
        for d in (FILLER if WITH_FILLER else ())
        for i, p in enumerate(d.pages)
    ),
]
TEXT_BY_REF = {p.ref: p.text for p in PAGES}
SCORED = [q for q in QUESTIONS if q.kind != "absence"]
KINDS = ("lookup", "paraphrase", "collision", "cross_doc")


def rrf(*ranked: list[tuple[str, int]], k: int = 60) -> list[tuple[str, int]]:
    """Reciprocal-rank fusion, matching what Qdrant does server-side."""
    scores: dict[tuple[str, int], float] = {}
    for lst in ranked:
        for rank, ref in enumerate(lst, start=1):
            scores[ref] = scores.get(ref, 0.0) + 1.0 / (k + rank)
    return [ref for ref, _ in sorted(scores.items(), key=lambda r: (-r[1], r[0]))]


def bm25_arm(question: str, limit: int) -> list[tuple[str, int]]:
    terms = search_terms(question)
    scored = [(score_text(p.text, terms), p.ref) for p in PAGES]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda r: (-r[0], r[1]))
    return [ref for _, ref in scored[:limit]]


@dataclass
class Result:
    recall: dict[int, float]
    mrr: float
    cited_distractor_first: bool


def score_one(ranked: list[tuple[str, int]], want: set[tuple[str, int]]) -> Result:
    recall = {k: (len(want & set(ranked[:k])) / len(want) if want else 1.0) for k in CUTOFFS}
    mrr = 0.0
    for rank, ref in enumerate(ranked, start=1):
        if ref in want:
            mrr = 1.0 / rank
            break
    first_is_distractor = bool(ranked) and ranked[0] not in want
    return Result(recall=recall, mrr=mrr, cited_distractor_first=first_is_distractor)


def report(label: str, results: dict[str, Result], timings: list[float]) -> None:
    print(f"\n=== {label} ===")
    for k in CUTOFFS:
        overall = statistics.mean(r.recall[k] for r in results.values())
        print(f"  recall@{k}: {overall:.2f}", end="   ")
    print(f"MRR: {statistics.mean(r.mrr for r in results.values()):.2f}")

    wrong_first = sum(1 for r in results.values() if r.cited_distractor_first)
    print(f"  top-ranked page was not a correct one: {wrong_first}/{len(results)}")

    print(f"  {'kind':11} {'@1':>5} {'@3':>5} {'@8':>5} {'MRR':>5}")
    for kind in KINDS:
        ids = [q.id for q in SCORED if q.kind == kind]
        if not ids:
            continue
        row = [statistics.mean(results[i].recall[k] for i in ids) for k in CUTOFFS]
        mrr = statistics.mean(results[i].mrr for i in ids)
        print(f"  {kind:11} {row[0]:5.2f} {row[1]:5.2f} {row[2]:5.2f} {mrr:5.2f}")

    if timings:
        print(
            f"  per-question: median {statistics.median(timings) * 1000:.0f}ms, "
            f"max {max(timings) * 1000:.0f}ms"
        )

    missed = [q for q in SCORED if results[q.id].recall[1] < 1.0]
    if missed:
        print(f"  not top-ranked ({len(missed)}):")
        for q in missed:
            r = results[q.id]
            print(
                f"    {q.id} [{q.kind:10}] @1={r.recall[1]:.2f} @8={r.recall[8]:.2f} "
                f"mrr={r.mrr:.2f}  {q.question[:46]}"
            )


def run_hashing() -> None:
    embedder = HashingEmbedder(1024)
    vectors = {p.ref: embedder.embed(p.text) for p in PAGES}
    results: dict[str, Result] = {}
    timings: list[float] = []

    for q in SCORED:
        t0 = time.perf_counter()
        qv = embedder.embed(q.question)
        dense = sorted(
            PAGES, key=lambda p: -sum(a * b for a, b in zip(qv, vectors[p.ref], strict=False))
        )
        fused = rrf([p.ref for p in dense[:CANDIDATES]], bm25_arm(q.question, CANDIDATES))
        timings.append(time.perf_counter() - t0)
        results[q.id] = score_one(fused, set(q.pages))
    report("1. hashing embedder + BM25, no rerank  (what runs today)", results, timings)


def run_dense(rerank: bool) -> tuple[dict[str, Result], list[float]]:
    from sentence_transformers import CrossEncoder, SentenceTransformer

    t0 = time.perf_counter()
    model = SentenceTransformer(EMBED_MODEL, device=DEVICE)
    print(f"  {EMBED_MODEL} loaded in {time.perf_counter() - t0:.1f}s")

    t0 = time.perf_counter()
    corpus_vectors = model.encode(
        [p.text for p in PAGES], normalize_embeddings=True, show_progress_bar=False
    )
    took = time.perf_counter() - t0
    print(f"  encoded {len(PAGES)} pages in {took:.2f}s ({took / len(PAGES) * 1000:.0f}ms/page)")

    cross = None
    if rerank:
        t0 = time.perf_counter()
        cross = CrossEncoder(RERANK_MODEL, device=DEVICE)
        print(f"  {RERANK_MODEL} loaded in {time.perf_counter() - t0:.1f}s")

    results: dict[str, Result] = {}
    timings: list[float] = []
    rerank_times: list[float] = []

    for q in SCORED:
        t0 = time.perf_counter()
        qv = model.encode(q.question, normalize_embeddings=True, show_progress_bar=False)
        sims = corpus_vectors @ qv
        order = sorted(range(len(PAGES)), key=lambda i: -sims[i])
        fused = rrf([PAGES[i].ref for i in order[:CANDIDATES]], bm25_arm(q.question, CANDIDATES))

        if cross is not None:
            pairs = [(q.question, TEXT_BY_REF[ref]) for ref in fused]
            r0 = time.perf_counter()
            scores = cross.predict(pairs, show_progress_bar=False)
            rerank_times.append(time.perf_counter() - r0)
            fused = [
                ref for ref, _ in sorted(zip(fused, scores, strict=False), key=lambda r: -r[1])
            ]

        timings.append(time.perf_counter() - t0)
        results[q.id] = score_one(fused, set(q.pages))

    if rerank_times:
        print(
            f"  reranking ~{CANDIDATES} candidates: median "
            f"{statistics.median(rerank_times) * 1000:.0f}ms, "
            f"max {max(rerank_times) * 1000:.0f}ms"
        )
    return results, timings


def main() -> None:
    real = sum(1 for p in PAGES if not p.distractor)
    docs = len(DOCUMENTS) + len(DISTRACTORS) + (len(FILLER) if WITH_FILLER else 0)
    print(
        f"corpus: {len(PAGES)} pages ({real} real + {len(PAGES) - real} distractor) "
        f"across {docs} documents"
    )
    print(f"questions: {len(SCORED)} scored, {len(QUESTIONS) - len(SCORED)} absence (excluded)")
    print(f"config: {CANDIDATES} candidates per arm -> RRF -> keep {KEEP}; device={DEVICE}")
    # The line that says whether a reranker measurement means anything: it is doing
    # its production job only when the candidate set is a shortlist.
    ratio = CANDIDATES / len(PAGES)
    regime = (
        f"shortlist — {CANDIDATES} candidates is {ratio:.0%} of the corpus"
        if ratio < 0.9
        else "GLOBAL RE-SORT — candidates ~= corpus, so reranker numbers do not "
        "describe its production job"
    )
    print(f"regime: {regime}")

    run_hashing()

    print("\n--- real models ---")
    results, timings = run_dense(rerank=False)
    report(f"2. {EMBED_MODEL} + BM25, no rerank", results, timings)

    if SKIP_RERANK:
        print("\n(reranker skipped: SPIKE_SKIP_RERANK=1)")
        return
    results, timings = run_dense(rerank=True)
    report(f"3. {EMBED_MODEL} + BM25 + {RERANK_MODEL}", results, timings)


if __name__ == "__main__":
    main()
