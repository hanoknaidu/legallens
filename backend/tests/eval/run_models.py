"""Compare embedding and reranking models on the same corpus, under the same protocol.

`run_spike.py` answered "is a real embedder worth it?" against one model pair. This
answers the follow-up — **which** open-weight models — and it exists because the
leaderboards cannot: MTEB and BEIR are averages over web, bio and finance corpora,
and nothing in them is a contract. A model that is third on MTEB may be first here.

Two things this harness does that a naive comparison gets wrong, both of which
change the numbers materially:

**Query prompts.** Instruction-tuned embedders (the Qwen3 family, E5, BGE) expect an
instruction prefix on the *query* and raw text on the *document*. Encoding both the
same way silently costs several points, and it is invisible — the model still returns
plausible vectors. `Qwen3-Embedding-0.6B` ships a `query` prompt in its config but
leaves `default_prompt_name` unset, so sentence-transformers will not apply it unless
asked. Each model below declares its own contract.

**Asymmetry.** Rerankers are scored on the *same* fused candidate set, so a reranker
row measures reordering alone and not the embedder underneath it.

Run:
    uv run python tests/eval/run_models.py              # everything cached
    MODELS_EMBED=minilm,qwen3 uv run python tests/eval/run_models.py
    MODELS_RERANK=none,bge_v2_m3 uv run python tests/eval/run_models.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from run_spike import (  # noqa: E402
    CANDIDATES,
    CUTOFFS,
    KINDS,
    PAGES,
    SCORED,
    TEXT_BY_REF,
    Result,
    bm25_arm,
    rrf,
    score_one,
)

DEVICE = os.environ.get("MODELS_DEVICE", "mps")

#: The instruction Qwen3 is given in place of its default, which is written for web
#: search ("Given a web search query, retrieve relevant passages"). The family is
#: trained to follow task instructions, so naming the actual task is free quality —
#: and naming the wrong one is a quiet tax.
LEGAL_INSTRUCT = (
    "Instruct: Given a question from a lawyer about a contract, retrieve the clause "
    "or provision that answers it\nQuery:"
)


@dataclass(frozen=True)
class EmbedModel:
    key: str
    name: str
    #: Prefix applied to queries only. Empty for symmetric models.
    query_prefix: str = ""
    note: str = ""


@dataclass(frozen=True)
class RerankModel:
    key: str
    name: str | None  # None = no reranking, the control arm
    note: str = ""


EMBEDDERS: tuple[EmbedModel, ...] = (
    EmbedModel(
        "minilm",
        "sentence-transformers/all-MiniLM-L6-v2",
        note="22M params, 384 dim, 256-token window. The incumbent, and the floor.",
    ),
    EmbedModel(
        "qwen3",
        "Qwen/Qwen3-Embedding-0.6B",
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages that "
            "answer the query\nQuery:"
        ),
        note="600M params, 1024 dim, 32K window. Its own default instruction.",
    ),
    EmbedModel(
        "qwen3_legal",
        "Qwen/Qwen3-Embedding-0.6B",
        query_prefix=LEGAL_INSTRUCT,
        note="The same weights, told what the task actually is.",
    ),
    EmbedModel(
        "bge_m3",
        "BAAI/bge-m3",
        note="568M params, 1024 dim, 8K window. Symmetric — no query prefix.",
    ),
)

RERANKERS: tuple[RerankModel, ...] = (
    RerankModel("none", None, note="Fusion order, untouched. The control."),
    RerankModel(
        "ms_marco",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        note="22M. Web-passage ranker; out of domain on legal prose.",
    ),
    RerankModel("bge_base", "BAAI/bge-reranker-base", note="278M, 512-token window."),
    RerankModel(
        "bge_v2_m3",
        "BAAI/bge-reranker-v2-m3",
        note="568M, 8K window. The plan's recommendation.",
    ),
    RerankModel(
        "qwen3_rr",
        "Qwen/Qwen3-Reranker-0.6B",
        note="600M, 32K window, instruction-following.",
    ),
)


@dataclass
class Row:
    embed: str
    rerank: str
    recall: dict[int, float]
    mrr: float
    wrong_first: int
    per_kind: dict[str, float]
    encode_ms: float = 0.0
    query_ms: float = 0.0
    failed: str = ""
    per_question: dict[str, Result] = field(default_factory=dict)


def _selected(env: str, available) -> list:
    wanted = os.environ.get(env, "")
    if not wanted:
        return list(available)
    keys = {k.strip() for k in wanted.split(",")}
    return [m for m in available if m.key in keys]


def fused_for(model: EmbedModel) -> tuple[dict[str, list[tuple[str, int]]], float, float]:
    """Dense + BM25 → RRF, for every scored question. Returns the fused candidates."""
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(model.name, device=DEVICE)

    started = time.perf_counter()
    corpus = encoder.encode(
        [p.text for p in PAGES], normalize_embeddings=True, show_progress_bar=False
    )
    encode_ms = (time.perf_counter() - started) / len(PAGES) * 1000

    fused: dict[str, list[tuple[str, int]]] = {}
    query_times: list[float] = []
    for question in SCORED:
        text = f"{model.query_prefix} {question.question}".strip()
        started = time.perf_counter()
        vector = encoder.encode(text, normalize_embeddings=True, show_progress_bar=False)
        similarity = corpus @ vector
        order = sorted(range(len(PAGES)), key=lambda i: -similarity[i])
        fused[question.id] = rrf(
            [PAGES[i].ref for i in order[:CANDIDATES]],
            bm25_arm(question.question, CANDIDATES),
        )
        query_times.append((time.perf_counter() - started) * 1000)

    del encoder
    return fused, encode_ms, statistics.median(query_times)


def score(fused: dict[str, list[tuple[str, int]]], embed_key: str, rerank_key: str) -> Row:
    results = {q.id: score_one(fused[q.id], set(q.pages)) for q in SCORED}
    return Row(
        embed=embed_key,
        rerank=rerank_key,
        recall={k: statistics.mean(r.recall[k] for r in results.values()) for k in CUTOFFS},
        mrr=statistics.mean(r.mrr for r in results.values()),
        wrong_first=sum(1 for r in results.values() if r.cited_distractor_first),
        per_kind={
            kind: statistics.mean(results[q.id].recall[1] for q in SCORED if q.kind == kind)
            for kind in KINDS
            if any(q.kind == kind for q in SCORED)
        },
        per_question=results,
    )


def _load_cross(model: RerankModel):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model.name, device=DEVICE)


def _apply(cross, fused: dict[str, list[tuple[str, int]]]):
    """Reorder every question's candidates with an already-loaded cross-encoder."""
    out: dict[str, list[tuple[str, int]]] = {}
    times: list[float] = []
    for question in SCORED:
        refs = fused[question.id]
        started = time.perf_counter()
        scores = cross.predict(
            [(question.question, TEXT_BY_REF[ref]) for ref in refs],
            show_progress_bar=False,
        )
        times.append((time.perf_counter() - started) * 1000)
        out[question.id] = [
            ref for ref, _ in sorted(zip(refs, scores, strict=True), key=lambda r: -r[1])
        ]
    return out, statistics.median(times)


#: Partial results land here as they are computed, so a crash or an interrupted
#: session does not throw away twenty minutes of GPU time.
RESULTS_TSV = Path(__file__).parent / "model-matrix.tsv"


def _write_tsv(rows: list[Row]) -> None:
    header = (
        "embedder\treranker\trecall@1\trecall@3\trecall@8\tmrr\twrong_first"
        "\tlookup\tparaphrase\tcollision\tcross_doc\tencode_ms\trerank_ms\n"
    )
    lines = [header]
    for r in rows:
        if r.failed:
            continue
        k = r.per_kind
        lines.append(
            f"{r.embed}\t{r.rerank}\t{r.recall[1]:.4f}\t{r.recall[3]:.4f}\t"
            f"{r.recall[8]:.4f}\t{r.mrr:.4f}\t{r.wrong_first}\t"
            f"{k.get('lookup', 0):.4f}\t{k.get('paraphrase', 0):.4f}\t"
            f"{k.get('collision', 0):.4f}\t{k.get('cross_doc', 0):.4f}\t"
            f"{r.encode_ms:.1f}\t{r.query_ms:.1f}\n"
        )
    RESULTS_TSV.write_text("".join(lines))


def main() -> None:
    embedders = _selected("MODELS_EMBED", EMBEDDERS)
    rerankers = _selected("MODELS_RERANK", RERANKERS)

    print(f"corpus: {len(PAGES)} pages, {len(SCORED)} scored questions, device={DEVICE}")
    print(f"{CANDIDATES} candidates per arm -> RRF -> rerank")
    print(f"{len(embedders)} embedders x {len(rerankers)} rerankers\n", flush=True)

    # Phase 1: every embedder's fused candidate set, computed once.
    #
    # The loop is embedder-outer/reranker-inner in the obvious writing, which reloads
    # each cross-encoder once per embedder — 20 model loads for a 4x5 matrix instead
    # of 9. Separating the phases means each model is constructed exactly once.
    fused_by_embed: dict[str, dict[str, list[tuple[str, int]]]] = {}
    encode_ms_by_embed: dict[str, float] = {}
    rows: list[Row] = []

    for embed in embedders:
        print(f"-- embed {embed.key}: {embed.name}", flush=True)
        try:
            fused, encode_ms, query_ms = fused_for(embed)
        except Exception as exc:  # noqa: BLE001 — one bad model must not stop the sweep
            print(f"   FAILED {type(exc).__name__}: {str(exc)[:110]}", flush=True)
            rows.append(
                Row(
                    embed.key, "-", {k: 0.0 for k in CUTOFFS}, 0.0, 0, {}, failed=type(exc).__name__
                )
            )
            continue
        fused_by_embed[embed.key] = fused
        encode_ms_by_embed[embed.key] = encode_ms
        print(f"   {encode_ms:.0f}ms/page encode, {query_ms:.0f}ms/query", flush=True)

    # Phase 2: each reranker loaded once, applied across every embedder's candidates.
    for rerank in rerankers:
        if rerank.name is None:
            for key, fused in fused_by_embed.items():
                row = score(fused, key, rerank.key)
                row.encode_ms = encode_ms_by_embed[key]
                rows.append(row)
                print(
                    f"-- {key:12} {rerank.key:10} recall@1={row.recall[1]:.2f} MRR={row.mrr:.2f}",
                    flush=True,
                )
            _write_tsv(rows)
            continue

        print(f"-- rerank {rerank.key}: {rerank.name}", flush=True)
        try:
            cross = _load_cross(rerank)
        except Exception as exc:  # noqa: BLE001
            print(f"   FAILED {type(exc).__name__}: {str(exc)[:110]}", flush=True)
            continue

        for key, fused in fused_by_embed.items():
            reordered, rerank_ms = _apply(cross, fused)
            row = score(reordered, key, rerank.key)
            row.encode_ms = encode_ms_by_embed[key]
            row.query_ms = rerank_ms
            rows.append(row)
            print(
                f"   {key:12} recall@1={row.recall[1]:.2f} recall@3={row.recall[3]:.2f} "
                f"MRR={row.mrr:.2f}  ({rerank_ms:.0f}ms/q)",
                flush=True,
            )
        del cross
        _write_tsv(rows)

    print("\n" + "=" * 92)
    print(
        f"{'embedder':13} {'reranker':11} {'@1':>5} {'@3':>5} {'@8':>5} {'MRR':>5} "
        f"{'bad1st':>7} {'look':>5} {'para':>5} {'coll':>5} {'xdoc':>5}"
    )
    print("-" * 92)
    for row in sorted(rows, key=lambda r: (-r.mrr, -r.recall[1])):
        if row.failed:
            print(f"{row.embed:13} {'-':11} FAILED: {row.failed}")
            continue
        k = row.per_kind
        print(
            f"{row.embed:13} {row.rerank:11} {row.recall[1]:5.2f} {row.recall[3]:5.2f} "
            f"{row.recall[8]:5.2f} {row.mrr:5.2f} {row.wrong_first:5}/18 "
            f"{k.get('lookup', 0):5.2f} {k.get('paraphrase', 0):5.2f} "
            f"{k.get('collision', 0):5.2f} {k.get('cross_doc', 0):5.2f}"
        )
    print("=" * 92)
    print("@1/@3/@8 = page recall at k - bad1st = wrong page ranked first")
    print("look/para/coll/xdoc = recall@1 by question kind")
    print(f"\nwritten to {RESULTS_TSV}")


if __name__ == "__main__":
    main()
