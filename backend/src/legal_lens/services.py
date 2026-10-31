"""The container, built once at lifespan.

Repositories and engines are constructed here rather than per request, and handed to
routers through `deps.py`. The point is substitutability: NFR-11 requires the model
provider, embedder and vector store to be swappable without touching an API contract,
which holds only if routers receive an interface they did not choose.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .ai.echo import EchoProvider
from .ai.pricing import cost_usd
from .ai.probe import UNCHECKED, ProbeResult, probe
from .ai.router import detect_intent
from .ai.types import GenerationProvider
from .chat.registry import PendingRegistry
from .config import Settings
from .db.repositories import (
    AuditRepository,
    ConversationRepository,
    DocumentRepository,
    MatterRepository,
    MessageRepository,
)
from .db.session import Database
from .ingestion import IngestionPipeline
from .ingestion.ocr import OcrEngine, engine_for
from .retrieval import MemoryVectorStore, embedder_for
from .retrieval.store import Indexed, VectorStore

if TYPE_CHECKING:  # pragma: no cover
    from .retrieval.orchestrated import OrchestratedRetrieval
from .schemas import Intent
from .storage import ObjectStore

log = logging.getLogger(__name__)


class Services:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.store = ObjectStore(settings.objects_path)

        self.matters = MatterRepository(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.documents = DocumentRepository(db)
        self.audit = AuditRepository(db)

        # Retrieval. `embedder_for` raises on an unimplemented provider rather than
        # falling back, so a misconfiguration fails at startup, not mid-answer.
        self.embedder = embedder_for(settings)
        self.vector_store: VectorStore = MemoryVectorStore()

        # The orchestrator's own index, built only when it is the selected provider:
        # it pulls torch and Qdrant, and constructing it otherwise would make every
        # boot pay for a pipeline nothing is going to call. Nothing heavy loads here
        # — see `OrchestratedRetrieval`, which defers that to first use.
        self.orchestrated: OrchestratedRetrieval | None = None
        if settings.llm_provider == "orchestrator":
            from .retrieval.orchestrated import OrchestratedRetrieval as _Orchestrated

            self.orchestrated = _Orchestrated(db, self.documents, settings)

        self.ocr: OcrEngine | None = self._build_ocr(settings)
        self.ingestion = IngestionPipeline(
            self.documents,
            self.store,
            settings,
            self.index_document,
            self.ocr,
            self.record_model_cost,
        )
        self.pending = PendingRegistry()
        # The model-backed intent router is built on first use, so a key that is only
        # needed by it is not demanded at boot.
        self._intent_classifier_built = False
        self._intent_classifier_instance: Any = None
        # Replaced during bootstrap by a real probe when one runs.
        self.llm_probe: ProbeResult = UNCHECKED
        self.generation_provider: GenerationProvider = self._build_provider(settings)

        # Background tasks are held here for their lifetime. asyncio keeps only a weak
        # reference to a bare `create_task`, so a long parse can be garbage collected
        # mid-run and leave the document stuck on `processing` with nothing logged.
        self._tasks: set[asyncio.Task] = set()

    def track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _build_ocr(self, settings: Settings) -> OcrEngine | None:
        """Build the OCR engine, or None when it is off or unavailable.

        Off is the default, and deliberately: FR-2 requires a scanned PDF to be
        rejected rather than transcribed.

        Two failure kinds, handled differently, because they are different mistakes:

        - **A missing credential or binary** degrades. The service still serves every
          read path, every native-text upload and every query, so refusing to boot
          over an optional ingestion feature would turn a small environment gap into
          a total outage. `llm_provider="anthropic"` without a key behaves the same
          way, and consistency matters more here than strictness.
        - **An unknown engine name** raises. That is a typo in configuration, and
          booting with a setting silently ignored is how a deployment ends up not
          doing what its config says.

        Either way `/health` reports the state, so a disabled engine is never
        mistaken for FR-2 rejection.
        """
        self.ocr_unavailable_reason: str | None = None
        if not settings.ocr_enabled:
            return None

        try:
            engine = engine_for(
                settings.ocr_engine,
                api_key=settings.anthropic_api_key,
                headers=settings.anthropic_headers,
            )
        except RuntimeError as exc:
            # Credential or environment gap: degrade loudly rather than crash.
            self.ocr_unavailable_reason = str(exc)
            log.warning("ocr enabled but unavailable", extra={"reason": str(exc)})
            return None

        log.info(
            "ocr enabled",
            extra={
                "engine": getattr(engine, "name", settings.ocr_engine),
                "max_pages": settings.ocr_max_pages,
            },
        )
        return engine

    def _build_provider(self, settings: Settings) -> GenerationProvider:
        """Select the generation provider (NFR-11).

        `echo` is a supported configuration, not a stub to be replaced: it needs no
        key, costs nothing and quotes the real documents, so the whole pipeline is
        demonstrable offline. The Anthropic provider is imported lazily so a missing
        SDK or key cannot stop the service from booting.
        """
        if settings.llm_provider == "echo":
            # A small per-token delay in dev so the streaming UI behaves as it will
            # against a real model; none under test, so the suite stays fast.
            return EchoProvider(delay_s=0.012 if settings.environment == "dev" else 0.0)

        if settings.llm_provider == "orchestrator":
            from .ai.orchestrator_provider import OrchestratorProvider

            assert self.orchestrated is not None  # built in __init__ for this provider
            return OrchestratorProvider(settings, self.orchestrated)

        from .ai.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)

    async def route_intent(self, owner_id: str, text: str) -> Intent:
        """Decide `ask`, `summary` or `draft`.

        The regex router is the default and runs in-process. The model router is a
        fourth model call on the path of every message, so three things are true of
        it and none are optional:

        - it runs in a **worker thread**, because `classify` is a synchronous
          LangChain call and this is an async route;
        - it **never raises** — a routing failure falls back to `ask` rather than
          failing the send, because a misrouted question still gets an answer while a
          500 loses the user's message;
        - it is **costed** (NFR-10), through `record_model_cost` — see there for why
          the row carries no `audit_id`.
        """
        if self.settings.intent_router == "regex":
            return detect_intent(text)

        classifier = self._intent_classifier()
        if classifier is None:
            return detect_intent(text)

        result = await asyncio.to_thread(classifier.classify, text)
        log.info(
            "intent classified",
            extra={
                "intent": result.intent,
                "confidence": result.confidence,
                "source": result.source,
                "latency_ms": result.latency_ms,
            },
        )
        if result.input_tokens or result.output_tokens or result.cache_read_tokens:
            await self.record_model_cost(
                owner_id=owner_id,
                purpose="intent",
                model_id=classifier.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                # The classifier's system prompt is cached, so on every message after
                # the first most of its input is a cache read. Priced at the read rate
                # rather than the input rate, which is a tenth of it.
                cache_read_tokens=result.cache_read_tokens,
                cache_write_tokens=result.cache_write_tokens,
            )
        return result.intent

    def _intent_classifier(self):
        """The Haiku classifier, built once, or None if it cannot be.

        Degrades to the regex router rather than refusing to boot, which is how a
        missing key is already handled for generation and OCR. `/health` reports the
        provider state, so a silent downgrade here is visible there.
        """
        if self._intent_classifier_built:
            return self._intent_classifier_instance

        self._intent_classifier_built = True
        try:
            from .orchestrator.intent import IntentClassifier

            self._intent_classifier_instance = IntentClassifier(
                model=self.settings.router_model,
                api_key=self.settings.anthropic_api_key,
                workspace_id=self.settings.anthropic_workspace_id,
            )
        except Exception as exc:  # noqa: BLE001 — falls back to the regex router
            log.warning(
                "intent_router='model' is unavailable; using the regex router",
                extra={"reason": str(exc)},
            )
            self._intent_classifier_instance = None
        return self._intent_classifier_instance

    async def bootstrap(self, owner_id: str) -> None:
        """First-boot seeding (FR-4). Idempotent, so it runs on every start."""
        await self.matters.ensure_default(owner_id)
        await self.requeue_interrupted()
        await self.rebuild_index()
        if self.orchestrated is not None:
            # Loads the page store and reconciles Qdrant against SQLite. Slow when
            # there is a gap to fill, and deliberately so: the alternative is a
            # service that boots fast and abstains on every question.
            await self.orchestrated.prepare()
        # Last, and never fatal: a provider that cannot answer degrades the service
        # rather than stopping it, exactly as a missing key already did.
        self.llm_probe = await probe(self.settings)

    async def index_document(self, document_id: str) -> int:
        """Embed and index one document's chunks. Called by the ingestion pipeline."""
        rows = await self.db.fetch_all(
            """SELECT id, document_id, matter_id, owner_id, page_start, page_end, text
                 FROM chunk_metadata WHERE document_id = ? ORDER BY ordinal""",
            (document_id,),
        )
        if not rows:
            return 0

        self.vector_store.remove_document(document_id)
        self.vector_store.upsert(
            [
                Indexed(
                    chunk_id=r["id"],
                    document_id=r["document_id"],
                    matter_id=r["matter_id"],
                    owner_id=r["owner_id"],
                    page_start=r["page_start"],
                    page_end=r["page_end"],
                    text=r["text"],
                    vector=self.embedder.embed(r["text"]),
                )
                for r in rows
            ]
        )
        # The orchestrator indexes the same chunk rows, so both sides agree about
        # what a citation points at. Done before `mark_chunks_indexed`, so a document
        # is never advertised as indexed while half of its index is missing.
        if self.orchestrated is not None:
            await self.orchestrated.index_document(document_id)

        await self.documents.mark_chunks_indexed(document_id)
        return len(rows)

    async def record_model_cost(
        self,
        *,
        owner_id: str,
        purpose: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """A model call that belongs to no chat turn.

        OCR runs during ingestion and intent routing runs before the assistant turn
        exists, so neither has an audit row to hang off. `audit_id` is null and the
        spend still lands in `usage_costs`, which is what NFR-10 asks for — a cost
        with no parent is still a cost.
        """
        await self.audit.record_cost(
            owner_id=owner_id,
            audit_id=None,
            purpose=purpose,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=cost_usd(
                model_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
            ),
        )

    def forget_document(self, document_id: str) -> bool:
        """Drop a deleted document from every index outside SQLite (NFR-8).

        The cascade inside SQLite takes pages, chunks and citations with the row, and
        the object store gets its own deletion job. The vector indexes are the third
        place the text lives, and the orchestrator's is the one that matters: Qdrant
        persists across a restart, so a document left in it keeps answering questions
        after the user deleted it. The in-memory store is rebuilt at boot from ready
        documents only, so it forgets by itself — but not until then, which is long
        enough to matter in a running process.

        Returns whether it is certain the text is gone, for the deletion job row.
        """
        try:
            self.vector_store.remove_document(document_id)
            if self.orchestrated is not None:
                self.orchestrated.remove_document(document_id)
        except Exception:
            log.exception("vector index purge failed", extra={"document": document_id})
            return False
        return True

    async def rebuild_index(self) -> int:
        """Repopulate the in-memory vector store at boot.

        It holds nothing across a restart, so without this `indexed = 1` in SQLite
        would be a claim about an index that does not exist — and retrieval would
        quietly return nothing on every question after a restart.
        """
        rows = await self.documents.indexable_chunks()
        if not rows:
            return 0
        self.vector_store.upsert(
            [
                Indexed(
                    chunk_id=r["id"],
                    document_id=r["document_id"],
                    matter_id=r["matter_id"],
                    owner_id=r["owner_id"],
                    page_start=r["page_start"],
                    page_end=r["page_end"],
                    text=r["text"],
                    vector=self.embedder.embed(r["text"]),
                )
                for r in rows
            ]
        )
        log.info("vector index rebuilt", extra={"chunks": len(rows)})
        return len(rows)

    async def requeue_interrupted(self) -> None:
        """Rescue documents left mid-parse by a restart.

        A row on `queued` or `processing` has no task behind it after the process dies,
        so it would be polled forever by a frontend waiting for a status that nothing
        is going to change. Marking them failed is honest and re-uploadable; silently
        leaving them is not.
        """
        rows = await self.db.fetch_all(
            "SELECT id FROM documents WHERE status IN ('queued', 'processing')"
        )
        for row in rows:
            await self.documents.mark_status(
                row["id"],
                "failed",
                "Indexing was interrupted when the server restarted. Re-upload to try again.",
            )
        if rows:
            log.info("requeued interrupted ingestions", extra={"count": len(rows)})

    async def shutdown(self) -> None:
        """Let in-flight parses finish, briefly, rather than killing them mid-write."""
        if self.orchestrated is not None:
            # Embedded Qdrant holds an exclusive lock on its directory; not releasing
            # it means the next boot cannot open its own index.
            self.orchestrated.close()
        if not self._tasks:
            return
        done, pending = await asyncio.wait(set(self._tasks), timeout=5.0)
        for task in pending:
            task.cancel()
        log.info(
            "background tasks drained",
            extra={"finished": len(done), "cancelled": len(pending)},
        )
