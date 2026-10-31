"""Runtime configuration.

Every provider choice in here is a seam, not a preference. BRD NFR-11 requires the
model provider, the embedding provider and the vector store to be swappable without
touching an API contract or the frontend, so each one is selected by a setting and
resolved through a factory in its own module.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Phase 1 is single-user with no login (BRD §6.1). The data model carries an owner
# on every matter and document regardless, and every query filters on it, so Phase 2
# authentication is a middleware change rather than a schema migration. This is the
# value that stands in for the authenticated subject until then.
PHASE1_OWNER_ID = "owner-local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="LEGAL_LENS_", extra="ignore")

    # ---- service -------------------------------------------------------------
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    # The Vite dev server. Phase 1 has no auth, so the browser sends no credentials
    # and this list exists to keep the API off arbitrary origins, not to protect a session.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- storage -------------------------------------------------------------
    data_dir: Path = Path("./var")
    sqlite_path: Path | None = None  # defaults to {data_dir}/legal-lens.db
    object_store_dir: Path | None = None  # defaults to {data_dir}/objects
    # The orchestrator's own Qdrant. Deliberately *not* the path in
    # `OrchestratorConfig` (`./var/qdrant-orchestrator`), which the CLI uses for the
    # eval corpus: embedded Qdrant takes an exclusive lock on its directory, so
    # sharing one would mean the CLI could not run while the service was up, and the
    # service would be searching eval fixtures if it ever won the race.
    orchestrator_qdrant_dir: Path | None = None  # defaults to {data_dir}/qdrant-live
    orchestrator_collection: str = "live_passages"

    # ---- model provider ------------------------------------------------------
    # `orchestrator` is the three-workflow pipeline in `orchestrator/`: it routes
    # intent, rewrites the query, retrieves hybrid and reranks, and picks its own
    # model per workflow. It needs the `workflows` extra (torch, Qdrant, fastembed),
    # which is why it is not the default — `anthropic` is the path that runs with no
    # local models, and `echo` the one that runs with no key at all.
    llm_provider: Literal["anthropic", "echo", "orchestrator"] = "anthropic"
    # Read under either name, from the environment or `.env`. Every other setting
    # here takes the `LEGAL_LENS_` prefix, which is right for settings that belong to
    # this service — but an API key belongs to the vendor, and the SDK, the `ant` CLI
    # and the orchestrator all read `ANTHROPIC_API_KEY`. An alias is what makes the
    # dotenv file work for both; a validator reading `os.environ` does not, because
    # the dotenv values never reach the process environment.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LEGAL_LENS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    # Required only for an API key that is not itself scoped to a workspace: such a
    # key authenticates fine and then fails every request with a 400 telling you to
    # name a workspace. Supported here so either kind of key works without the
    # account having to be reorganised.
    anthropic_workspace_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LEGAL_LENS_ANTHROPIC_WORKSPACE_ID", "ANTHROPIC_WORKSPACE_ID"
        ),
    )
    # Which router decides `ask` / `summary` / `draft`. The regex is not a
    # placeholder — it mirrors the frontend mock exactly, is deterministic, free and
    # instant, and is what has to run under `llm_provider="echo"`. `model` is the
    # Haiku classifier in `orchestrator/intent.py`, which handles the phrasings a
    # keyword cannot: "what are the key terms" is a summary, "draft me a clause that
    # caps liability" is a draft, and a regex that keys on "key terms" or "draft"
    # gets both of those right for the wrong reason and the next two wrong.
    intent_router: Literal["regex", "model"] = "regex"
    # Generation, drafting, summary. The citation-bearing calls.
    generation_model: str = "claude-opus-5-5"
    # Intent routing and query rewriting: mechanical, high-volume, latency-sensitive.
    # Cheap tiers are candidates for mechanical routes only once quality is measured
    # per NFR-1 — these two are mechanical by construction, and the move from Haiku
    # 4.5 to 5.5 was measured against `--suite` and `--rewrite-suite` before it
    # landed.
    #
    # 5.5 specifically because this prompt is 1742 tokens and 4.5's minimum
    # cacheable prefix is 4096: a cache breakpoint on it was a silent no-op. 5.5's
    # minimum is 512. See `orchestrator/config.py` REWRITE_MODEL for the long version.
    router_model: str = "claude-haiku-5-5"
    generation_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    max_output_tokens: int = 8000
    draft_max_output_tokens: int = 16000

    # Probe the model provider once at startup with a one-token request, so an
    # unusable key is reported as `degraded` rather than as healthy. Without it
    # `/health` only tests whether a key is *present*: a revoked key, an exhausted
    # credit balance and a key that needs a workspace header all read as "ok" while
    # every generation fails. Skipped automatically under `environment="test"`, and
    # never run for `llm_provider="echo"`, which makes no vendor call.
    probe_llm_on_startup: bool = True
    # Kept short: boot should not hang on a slow vendor, and a timeout is reported as
    # "could not verify" rather than as a bad key.
    probe_timeout_seconds: float = 10.0

    # ---- retrieval -----------------------------------------------------------
    vector_store: Literal["qdrant", "memory"] = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "chunks"
    # "hashing" is a deterministic, dependency-free stand-in so the pipeline runs
    # end-to-end offline. It is NOT a semantic embedder — see ai/embeddings.py, and
    # BRD §8.2: a real embedding vendor is a second processor of privileged text and
    # needs its own data-processing agreement before it is switched on.
    embedding_provider: Literal["hashing", "local", "voyage"] = "hashing"
    embedding_model: str = "voyage-law-2"
    embedding_dimensions: int = 1024
    voyage_api_key: str | None = None

    # ---- retrieval strategy threshold (BRD §4.2) -----------------------------
    # Below this, the whole corpus goes into context: nothing can be missed, page
    # citations come back natively, and questions about absence are answerable.
    # Above it, RAG earns its place. The threshold selects per request.
    #
    # This one governs `select_pages`, which is the `echo` and `anthropic` path. The
    # orchestrator makes the same §4.2 decision against its own
    # `ORCH_WHOLE_DOCUMENT_MAX_TOKENS`, which defaults to 120_000 — deliberately
    # lower, because at 600K almost nothing in a realistic matter would ever take the
    # retrieval arm and a hybrid pipeline that never runs is one nobody has tested.
    # Two numbers for one decision is a real cost; the alternative is the orchestrator
    # importing this module, which is what keeps it runnable with nothing else booted.
    # If you tune one, look at the other.
    whole_document_token_budget: int = 600_000
    retrieval_candidates: int = 50  # top-k per retrieval arm before fusion
    retrieval_top_n: int = 8  # chunks surviving rerank into the prompt
    rerank_min_score: float = 0.02

    # ---- ingestion limits (FR-1, NFR-5) --------------------------------------
    parser: Literal["docling", "builtin"] = "builtin"

    # ---- OCR (FR-2) ----------------------------------------------------------
    # Off by default, and that default is the BRD's decision rather than an
    # oversight: FR-2 requires a scanned PDF to be *rejected*, because
    # "the silent-failure mode is worse than a clear rejection". Turning this on
    # trades a clear rejection for text whose provenance is a transcription — see
    # ingestion/ocr.py for what that costs a citation-grounded product.
    ocr_enabled: bool = False
    # `claude` needs no system binary and adds no new processor of privileged text,
    # since that vendor already receives document text. `tesseract` makes no vendor
    # call at all but needs the binary installed, and is weaker on poor scans.
    ocr_engine: Literal["none", "tesseract", "claude"] = "claude"
    ocr_vision_model: str = "claude-haiku-4-5"
    ocr_dpi: int = 300
    # OCR costs a call and seconds per page, so a 500-page scan is refused rather
    # than silently becoming the most expensive upload in the system.
    ocr_max_pages: int = 50
    # Above this share of [ILLEGIBLE] markers the transcription is not trustworthy
    # enough to cite, and the document is rejected as if OCR had not run.
    ocr_max_illegible_ratio: float = 0.25
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    max_documents_per_matter: int = 200
    # A page yielding fewer extractable characters than this is treated as having no
    # text layer. Scanned pages typically yield 0; a sparse cover page can yield a few.
    min_chars_per_text_page: int = 24
    chunk_target_chars: int = 2800
    chunk_overlap_chars: int = 240

    # ---- limits --------------------------------------------------------------
    rate_limit_per_minute: int = 60
    audit_retention_days: int = 365  # NFR-9

    @model_validator(mode="after")
    def _inherit_plain_anthropic_key(self) -> Settings:
        """Accept the unprefixed `ANTHROPIC_API_KEY` as well.

        Every setting here is read with a `LEGAL_LENS_` prefix, which is right for
        settings that belong to this service. An API key is not one of those: it
        belongs to the vendor, the SDK and the `ant` CLI all read
        `ANTHROPIC_API_KEY`, and so does the orchestrator. Requiring the same secret
        under two names is how someone ends up setting one, seeing no effect, and
        concluding the feature is broken.

        The prefixed form still wins where both are set, so a deployment can override
        the ambient key for this service alone.
        """
        if not self.anthropic_api_key:
            # Belt and braces: the alias covers the environment and `.env`; this
            # covers a key exported after the settings class was imported.
            ambient = os.environ.get("ANTHROPIC_API_KEY")
            if ambient:
                object.__setattr__(self, "anthropic_api_key", ambient)
        return self

    @property
    def anthropic_headers(self) -> dict[str, str]:
        """Extra headers every Anthropic client in this service should send."""
        if self.anthropic_workspace_id:
            return {"anthropic-workspace-id": self.anthropic_workspace_id}
        return {}

    @property
    def db_path(self) -> Path:
        return self.sqlite_path or self.data_dir / "legal-lens.db"

    @property
    def objects_path(self) -> Path:
        return self.object_store_dir or self.data_dir / "objects"

    @property
    def orchestrator_qdrant_path(self) -> Path:
        return self.orchestrator_qdrant_dir or self.data_dir / "qdrant-live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
