"""Configuration for the orchestration layer.

Separate from `legal_lens.config` on purpose. This package imports nothing from the
rest of the service, which is what lets a prompt be tuned by running one CLI command
instead of booting an API, a database and an object store. Carrying its own settings
is the price of that, and it is a small price: these are read from the environment
with plain `os.environ`, under an `ORCH_` prefix so they cannot collide with the
`LEGAL_LENS_` settings the service uses.

Every model choice here is explicit rather than inherited from a shared
`generation_model`, because the four calls in these workflows want four different
tiers and a single setting would force them to agree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Haiku 5.5. The query rewrite is mechanical: resolve a pronoun against the previous
#: turns, or decline to. There is nothing here for a larger model to be better at.
#:
#: **Haiku 5.5 rather than 4.5, and the reason is caching, not capability.** The
#: minimum cacheable prefix is per-model and not monotonic across generations: 4.5
#: requires 4096 tokens, 5.5 requires 512. This prompt measures 1274 tokens, so on
#: 4.5 a cache breakpoint on it cached nothing at all — silently, with no error and
#: `cache_creation_input_tokens: 0`. On 5.5 it caches, and the model is also a tenth
#: of the input price. Measured: the routing steps were 17% of a turn's cost on 4.5
#: with every token fresh on every message.
#:
#: Hard constraints: `output_config.effort` is rejected on this tier, and a
#: non-default `temperature` is a **400** on 5.5 — see `QueryRewriter.__init__`.
REWRITE_MODEL = "claude-haiku-5-5"

#: Sonnet 5.5 for `ask`.
#:
#: Hard constraint: `thinking: {"type": "disabled"}` returns a 400. Thinking is left
#: at its adaptive default and depth is controlled with `output_config.effort`.
ASK_MODEL = "claude-sonnet-5-5"

#: Summary routes between these two. See `summary_model_for`.
SUMMARY_MODEL_SMALL = "claude-sonnet-5-5"
SUMMARY_MODEL_LARGE = "claude-opus-5-5"

#: Opus 5.5 for `draft`.
#:
#: Hard constraints: thinking cannot be disabled at any effort, `budget_tokens` is a
#: 400, and the default effort is `medium` — so `high` has to be set explicitly or the
#: call silently buys less than was asked for.
DRAFT_MODEL = "claude-opus-5-5"

# ---------------------------------------------------------------------------
# Local models
# ---------------------------------------------------------------------------
#
# Both run in-process, which is not only a preference: BRD §8.2 treats an embedding
# vendor as a second processor of privileged attorney-client text, needing its own
# data-processing agreement before it is switched on. An open-weight model running on
# this machine sidesteps that entirely.
#
# Chosen by measurement, not by leaderboard — `tests/eval/run_models.py` compares
# four embedders against five rerankers on the contract corpus. MTEB and BEIR are
# averages over web, bio and finance text, and none of it is a contract.
#
#   embedder (no rerank)                recall@1   recall@3   MRR
#   all-MiniLM-L6-v2  (the incumbent)     0.61       0.83     0.83
#   Qwen3-Embedding-0.6B  + its prompt    0.67       0.83     0.85
#   Qwen3-Embedding-0.6B  no prompt       0.61       0.83     0.83
#   bge-m3                                0.67       0.83     0.85
#
# `bge-m3` and `Qwen3-Embedding-0.6B` tie, and on 18 questions a tie is a tie — one
# question is 0.06 of recall@1, so this is not a quality decision. The tiebreakers:
#
#   * **bge-m3 is symmetric.** It declares no query prompt, so there is no prompt
#     contract to get silently wrong. Qwen3 ships one and leaves
#     `default_prompt_name` unset, which cost 0.06 recall@1 until it was found —
#     see `DenseEncoder`. That failure is now structurally prevented, but a model
#     that cannot have it at all is still worth something.
#   * **MIT rather than Apache-2.0.** Both are fine for commercial use over
#     privileged text; neither is a constraint.
#   * 8K context against Qwen3's 32K, which sounds like a loss and is not: chunks are
#     ~2800 chars, roughly 700 tokens, so 8K is already an order of magnitude of
#     headroom. The context argument in `workflows-plan.md` §3 was aimed at
#     512-token encoders like MiniLM, and it is right about those.
#
# Qwen3's advantage is upward: a 4B and an 8B share its prompt contract, so growing
# into one is a settings change. bge-m3 has no larger sibling. Revisit on a GPU.
#
# Not chosen, and worth recording so nobody re-litigates them:
#
#   * **LEGAL-BERT and the legal-domain encoders.** The intuitive pick for a
#     contracts product, and currently the wrong one — they cap at 512 tokens, which
#     breaks clause-level semantics, and general models now beat them on clause
#     retrieval.
#   * **jina-reranker-v3.** The strongest open reranker measured anywhere right now,
#     and CC-BY-NC-4.0. Non-commercial, so it is unusable here whatever it scores.

DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"ORCH_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"ORCH_{name}")
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(f"ORCH_{name}")
    return float(raw) if raw else default


@dataclass(frozen=True)
class OrchestratorConfig:
    """Everything the three workflows need, and nothing the service needs."""

    # ---- models ----------------------------------------------------------
    rewrite_model: str = field(default_factory=lambda: _env("REWRITE_MODEL", REWRITE_MODEL))
    ask_model: str = field(default_factory=lambda: _env("ASK_MODEL", ASK_MODEL))
    summary_model_small: str = field(
        default_factory=lambda: _env("SUMMARY_MODEL_SMALL", SUMMARY_MODEL_SMALL)
    )
    summary_model_large: str = field(
        default_factory=lambda: _env("SUMMARY_MODEL_LARGE", SUMMARY_MODEL_LARGE)
    )
    draft_model: str = field(default_factory=lambda: _env("DRAFT_MODEL", DRAFT_MODEL))

    # ---- effort ----------------------------------------------------------
    # Sonnet 5.5's effort levels are recalibrated from Sonnet 5, so this number is a
    # starting point to tune against the eval rather than one to port from elsewhere.
    ask_effort: str = field(default_factory=lambda: _env("ASK_EFFORT", "medium"))
    # Both of these are set explicitly because Opus 5.5 defaults to `medium`.
    summary_effort: str = field(default_factory=lambda: _env("SUMMARY_EFFORT", "high"))
    draft_effort: str = field(default_factory=lambda: _env("DRAFT_EFFORT", "high"))

    max_output_tokens: int = field(default_factory=lambda: _env_int("MAX_OUTPUT_TOKENS", 8000))
    # A memo is longer than an answer, and truncating one mid-clause produces a
    # document that looks finished and is not.
    draft_max_output_tokens: int = field(
        default_factory=lambda: _env_int("DRAFT_MAX_OUTPUT_TOKENS", 16000)
    )

    #: Two or more documents in scope, or an amendment anywhere in it, routes summary
    #: to the larger model. Summarising one contract is extraction; reconciling an
    #: amendment against the agreement it changes is the case that needs the stronger
    #: model, and page count does not detect it.
    summary_opus_above_documents: int = field(
        default_factory=lambda: _env_int("SUMMARY_OPUS_ABOVE_DOCUMENTS", 2)
    )

    # ---- local models ----------------------------------------------------
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", DEFAULT_EMBED_MODEL))
    rerank_model: str = field(default_factory=lambda: _env("RERANK_MODEL", DEFAULT_RERANK_MODEL))

    #: Overrides the instruction prefix applied to queries. Leave unset: the encoder
    #: reads the model's own declared `query` prompt, which is right for every model
    #: tested and is empty for symmetric ones like `bge-m3`.
    #:
    #: Set it only with a measurement in hand. A hand-written legal instruction
    #: ("Given a question from a lawyer about a contract, retrieve the clause that
    #: answers it") scored recall@1 **0.61** against Qwen3's own generic default at
    #: **0.67** on this corpus. Instruction tuning is a real lever and a plausible
    #: instruction is not automatically a good one.
    query_prompt: str | None = field(
        default_factory=lambda: os.environ.get("ORCH_QUERY_PROMPT") or None
    )

    #: Whether the cross-encoder runs at all. `ORCH_RERANK_ENABLED=0` turns it off.
    #:
    #: **It stays on, and as of 2026-10-19 there is finally a reason.** It had been on
    #: against a measurement that said it hurt — a measurement taken in the wrong
    #: regime, which the eval could not express until `tests/eval/filler.py` enlarged
    #: the corpus to 105 pages. With 25 candidates per arm against 25 pages, fusion
    #: handed the reranker the entire corpus and it was globally re-sorting; a
    #: production reranker refines a shortlist. Re-measured where candidates are 24%
    #: of the corpus, on `bge-m3` (`tests/eval/run_models.py`):
    #:
    #:   reranker                   @1     @3     @8    MRR   bad1st   per query
    #:   none (fusion order)       0.56   0.72   0.89   0.75    6/18        -
    #:   ms-marco-MiniLM-L-6-v2    0.56   0.83   0.92   0.79    6/18     ~150ms
    #:   bge-reranker-base         0.33   0.67   0.92   0.60   11/18    ~1000ms
    #:   **bge-reranker-v2-m3**  **0.64 **0.83 **0.92 **0.84  **4/18**  ~3550ms
    #:
    #: `bge-reranker-v2-m3` now **wins on every metric**, where at 25 pages it lost
    #: recall@1 (0.64 against 0.67) and the trade had to be argued for. It is no
    #: longer a trade. Two things the enlarged corpus revealed:
    #:
    #:   * **No reranking degrades as the corpus grows** — recall@1 0.67 -> 0.56 —
    #:     while reranking holds at 0.64. The reranker's value is a function of how
    #:     much there is to get wrong, which is why the small-corpus measurement
    #:     understated it and why it will understate it again on any eval that is
    #:     smaller than a real matter.
    #:   * **`ms-marco` is not harmful here**, which reverses the earlier reading of
    #:     it as noise: +0.11 recall@3 and +0.04 MRR for 150ms against 3550ms. If
    #:     latency ever stops being deprioritised, it is the first thing to measure
    #:     properly rather than the thing to dismiss.
    #:
    #: `bge-reranker-base` remains actively harmful (recall@1 0.33, wrong page first
    #: on 11 of 18) and should not be reached for because it shares a name with the
    #: one that works.
    #:
    #: **The one real cost, and it is consistent across every reranker: `paraphrase`
    #: gets worse.** For `bge-reranker-v2-m3`, `collision` goes 0.50 -> **1.00** —
    #: exactly the job a cross-encoder exists for, separating clauses whose words
    #: match a question they do not answer — while `paraphrase` goes 0.58 -> **0.33**.
    #: Every reranker measured shows the same direction. A question phrased in none of
    #: the document's words is found by the dense arm and then demoted by the
    #: cross-encoder, and nothing here fixes that; it is the open item worth the next
    #: measurement.
    #:
    #: At ~3.5s per question this is the slowest step in the pipeline, and it only
    #: runs on corpora above `whole_document_max_tokens`. Latency is deprioritised by
    #: instruction, so it stays on.
    rerank_enabled: bool = field(
        default_factory=lambda: _env("RERANK_ENABLED", "1") not in {"0", "false", "no"}
    )
    #: `mps` on Apple silicon, `cpu` elsewhere, or whatever is forced here.
    device: str | None = field(default_factory=lambda: os.environ.get("ORCH_DEVICE") or None)

    # ---- retrieval -------------------------------------------------------
    #: Above this many estimated tokens in scope, `ask` and `draft` retrieve; at or
    #: below it they read every page. BRD §4.2 selects per request this way, and the
    #: reason is FR-13 rather than cost: *"the contract is silent on insurance"* is
    #: only provable if the whole contract was read. Hybrid retrieval keeps `ask_top_n`
    #: passages, so on anything larger than a short agreement `coverage` is false and
    #: every absence question abstains — measured on a 28-page MSA, which fits this
    #: budget eight times over.
    #:
    #: 120K rather than the BRD's 600K, per `workflows-plan.md` §1: at 600K almost
    #: nothing in a realistic matter would ever exercise the hybrid path, and a
    #: retrieval pipeline that never runs is a retrieval pipeline nobody has tested.
    #: `0` forces retrieval always, which is what the measurements in §9.2 were taken
    #: under.
    whole_document_max_tokens: int = field(
        default_factory=lambda: _env_int("WHOLE_DOCUMENT_MAX_TOKENS", 120_000)
    )
    #: Per arm, before fusion. Both arms ask for this many; RRF merges them.
    candidates: int = field(default_factory=lambda: _env_int("CANDIDATES", 25))
    #: Surviving the reranker into an `ask` prompt.
    ask_top_n: int = field(default_factory=lambda: _env_int("ASK_TOP_N", 8))
    #: Wider for `draft`: a memo cites more ground than an answer does, and a clause
    #: that never reached the prompt becomes an `[ATTORNEY INPUT REQUIRED]` the
    #: attorney has to research by hand.
    draft_top_n: int = field(default_factory=lambda: _env_int("DRAFT_TOP_N", 12))
    #: The cross-encoder's floor, or `None` for no floor — which is the default, and
    #: deliberately so. **A floor is only meaningful on a known score scale**, and
    #: cross-encoders do not agree on one: `ms-marco-MiniLM-L-6-v2` returns raw
    #: logits that are routinely in the -11..0 range even for the passage that
    #: answers the question, while `bge-reranker-v2-m3` returns something closer to
    #: 0..1. A floor of 0.0 against the former silently discards everything, which is
    #: a retrieval outage that looks like a quiet model.
    #:
    #: Set it only after looking at real scores from the reranker actually in use.
    rerank_min_score: float | None = field(
        default_factory=lambda: (
            float(os.environ["ORCH_RERANK_MIN_SCORE"])
            if os.environ.get("ORCH_RERANK_MIN_SCORE")
            else None
        )
    )

    #: Embedded Qdrant. A path rather than a URL means no server and no Docker; set
    #: `ORCH_QDRANT_URL` to use a running instance instead.
    qdrant_path: Path = field(
        default_factory=lambda: Path(_env("QDRANT_PATH", "./var/qdrant-orchestrator"))
    )
    qdrant_url: str | None = field(default_factory=lambda: os.environ.get("ORCH_QDRANT_URL"))
    qdrant_collection: str = field(default_factory=lambda: _env("QDRANT_COLLECTION", "passages"))

    # ---- credentials -----------------------------------------------------
    api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    #: An API key that is not scoped to a workspace authenticates fine and then fails
    #: every request with a 400 naming this header.
    workspace_id: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_WORKSPACE_ID")
    )

    timeout: float = field(default_factory=lambda: _env_float("TIMEOUT", 120.0))

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Put it in backend/.env "
                "(see .env.example) or export it."
            )
        return self.api_key

    @property
    def headers(self) -> dict[str, str] | None:
        return {"anthropic-workspace-id": self.workspace_id} if self.workspace_id else None

    def summary_model_for(self, *, documents: int, has_amendment: bool) -> str:
        """Route summary by how much reconciliation the answer needs.

        Not by page count: a 90-page agreement summarised alone is still extraction,
        while a 4-page amendment read against the agreement it changes is the hard
        case — two documents that contradict each other, where naming the superseded
        figure is a confident wrong answer.
        """
        if has_amendment or documents >= self.summary_opus_above_documents:
            return self.summary_model_large
        return self.summary_model_small
