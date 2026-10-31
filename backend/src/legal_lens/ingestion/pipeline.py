"""The ingestion pipeline: queued → processing → ready, or failed with a reason.

Runs as a background task rather than inside the upload request. A 40-page MSA takes
seconds to parse, and holding the POST open for it would mean a 50MB upload followed
by a spinner with no progress — the frontend polls `/status` every two seconds
precisely so this can happen out of band.

**A failure here is a document state, not an exception.** FR-2's scanned PDF is the
case that matters: the row persists with `status: "failed"` and the verbatim reason,
because a user told only "failed" re-uploads the same file.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from ..clock import utcnow
from ..config import Settings
from ..db.repositories.documents import DocumentRepository
from ..observability import METRICS
from ..storage import ObjectStore
from .chunk import chunk_pages
from .constants import (
    EMPTY_DOCUMENT_REASON,
    INTERNAL_FAILURE_REASON,
    NO_TEXT_LAYER_REASON,
    OCR_EMPTY_REASON,
    OCR_TOO_MANY_PAGES_REASON,
    OCR_UNREADABLE_REASON,
    TOO_MANY_PAGES_REASON,
    UNREADABLE_REASON,
    suffix_of,
)
from .ocr import ILLEGIBLE, OcrEngine
from .parse import ParsedPage, ParseResult, UnreadableFile, parse

log = logging.getLogger(__name__)

# Rough chars-per-token. Only used to decide whole-corpus versus RAG retrieval
# (`whole_document_token_budget`), where being 20% out changes nothing.
CHARS_PER_TOKEN = 4


class IngestionPipeline:
    def __init__(
        self,
        documents: DocumentRepository,
        store: ObjectStore,
        settings: Settings,
        index: Callable[[str], Awaitable[int]] | None = None,
        ocr: OcrEngine | None = None,
        record_cost: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._documents = documents
        self._store = store
        self._settings = settings
        # Injected so the pipeline neither builds nor knows about a vendor client,
        # and so a test can supply a deterministic engine.
        self._ocr = ocr
        # Injected rather than imported: the pipeline must not reach back into the
        # service container, and ingestion has to remain runnable without retrieval.
        # Named for what it does — `_index` is this class's own method.
        self._index_vectors = index
        # Injected for the same reason: NFR-10 wants every model call costed, and OCR
        # is the only model call ingestion makes — but the pipeline must not reach
        # into the service container to find the audit repository.
        self._record_cost = record_cost

    async def run(self, owner_id: str, document_id: str, object_key: str, filename: str) -> None:
        started = utcnow()
        try:
            await self._documents.mark_status(document_id, "processing")
            data = self._store.get(object_key)
            if data is None:
                # The row exists but its bytes do not. Recoverable only by re-upload,
                # so say that rather than retrying forever.
                await self._fail(document_id, UNREADABLE_REASON, "missing_object")
                return

            result = self._parse(data, filename)
            if result is None:
                await self._fail(document_id, UNREADABLE_REASON, "unreadable")
                return

            text_source = "native"

            # FR-2's fork. A PDF that parsed to nothing is a scan. Without OCR
            # configured it is rejected, which is the BRD's decision and the default.
            # With OCR configured, the scan takes the second branch and rejoins the
            # same chunk -> embed -> index path below.
            if result.is_scan and suffix_of(filename) == ".pdf" and self._ocr is not None:
                recovered = await self._ocr_pages(owner_id, document_id, data, result)
                if recovered is None:
                    return
                result, text_source = recovered, "ocr"

            verdict = self._verdict(result, suffix_of(filename))
            if verdict is not None:
                await self._documents.replace_pages(document_id, [])
                await self._fail(document_id, verdict, "rejected", pages=result.page_count)
                return

            await self._index(owner_id, document_id, result, text_source=text_source)

            elapsed_ms = (utcnow() - started).total_seconds() * 1000
            METRICS.observe("ingest_ms", elapsed_ms, suffix=suffix_of(filename))
            METRICS.incr("ingest", outcome="ready")
            log.info(
                "document ingested",
                extra={
                    "document_id": document_id,
                    "pages": result.page_count,
                    "chars": result.total_chars,
                    "ms": round(elapsed_ms, 2),
                },
            )
        except Exception:
            # The task owns this document's state. If it dies without setting one, the
            # row sits on `processing` forever and the frontend polls it forever.
            log.exception("ingestion crashed", extra={"document_id": document_id})
            METRICS.incr("ingest", outcome="crashed")
            # Deliberately not the "file may be corrupt" wording: this is our bug,
            # not their file, and telling them to re-encode a good document wastes
            # their time and hides ours.
            await self._fail(document_id, INTERNAL_FAILURE_REASON, "crashed")

    def _parse(self, data: bytes, filename: str) -> ParseResult | None:
        try:
            return parse(
                data,
                suffix_of(filename),
                min_chars_per_text_page=self._settings.min_chars_per_text_page,
                page_budget_chars=self._settings.chunk_target_chars,
            )
        except UnreadableFile as exc:
            # `file` rather than `filename`: `filename` is a reserved LogRecord
            # attribute, and logging raises KeyError on it before any formatter runs —
            # which turned every unreadable upload into a crash on its own log line.
            log.warning("file unreadable", extra={"file": filename, "reason": str(exc)})
            return None

    def _verdict(self, result: ParseResult, suffix: str) -> str | None:
        """The reason to reject, or None to index.

        Ordered most specific first, and the FR-2 check has to come before the generic
        empty-document one: a scan parses to pages that are all empty, so a
        `total_chars == 0` test placed earlier would catch it and report "there is
        nothing to index" — true, but useless. FR-2 exists to tell the user the file is
        a scan and that OCR is the way out, which is advice they can act on.
        """
        # Nothing parsed at all: not even a page structure. There is no scan verdict to
        # reach, because no page was read to be found empty.
        if result.page_count == 0:
            return EMPTY_DOCUMENT_REASON

        # FR-2, and only for PDFs: a DOCX or TXT with no text is empty rather than
        # scanned, and telling someone to OCR a .txt is advice they cannot act on.
        if suffix == ".pdf" and result.empty_pages == result.page_count:
            return NO_TEXT_LAYER_REASON

        if result.total_chars == 0:
            return EMPTY_DOCUMENT_REASON

        if result.page_count > self._settings.max_pages:
            return TOO_MANY_PAGES_REASON.format(
                pages=result.page_count, limit=self._settings.max_pages
            )

        return None

    async def _ocr_pages(
        self, owner_id: str, document_id: str, data: bytes, parsed: ParseResult
    ) -> ParseResult | None:
        """Transcribe a scan, or reject it. `None` means the document was failed.

        Runs in a thread: both engines are blocking — one shells out to tesseract,
        the other makes a vendor call per page — and holding the event loop for a
        30-page scan would stall every other request including the SSE streams.
        """
        import asyncio

        from .rasterize import RasterizeUnavailable, page_images

        if parsed.page_count > self._settings.ocr_max_pages:
            # OCR costs a call and seconds per page, so the largest scans are refused
            # rather than silently becoming the most expensive upload in the system.
            await self._fail(
                document_id,
                OCR_TOO_MANY_PAGES_REASON.format(
                    pages=parsed.page_count, limit=self._settings.ocr_max_pages
                ),
                "ocr_too_large",
                pages=parsed.page_count,
            )
            return None

        try:
            images = await asyncio.to_thread(
                page_images,
                data,
                dpi=self._settings.ocr_dpi,
                max_pages=self._settings.ocr_max_pages,
            )
            pages = await asyncio.to_thread(self._ocr.transcribe, images)
        except RasterizeUnavailable as exc:
            log.error("ocr unavailable", extra={"document_id": document_id, "reason": str(exc)})
            await self._fail(document_id, INTERNAL_FAILURE_REASON, "ocr_unavailable")
            return None

        transcribed = ParseResult(
            pages=[ParsedPage(page=p.page, text=p.text) for p in pages],
            empty_pages=sum(1 for p in pages if p.is_empty),
        )

        # Billed before any verdict below. A scan rejected for illegibility still
        # cost a call per page, and a rejection that spent money silently is how OCR
        # ends up as an unexplained line on the invoice (NFR-10).
        await self._bill_ocr(owner_id, pages)

        # Pages the model described instead of transcribing. The descriptions are
        # already discarded (`transcription_of`), so this is a signal about the whole
        # transcription: a model that ignored rule 1 here was free to ignore rule 5 —
        # mark a gap rather than invent one — on a page that *did* have text.
        narrated = sum(1 for p in pages if p.narrated)
        if narrated:
            log.warning(
                "ocr described pages instead of transcribing them",
                extra={
                    "document_id": document_id,
                    "narrated_pages": narrated,
                    "pages": transcribed.page_count,
                },
            )
            METRICS.incr("ocr_narrated_pages", narrated)

        if transcribed.total_chars == 0:
            await self._fail(
                document_id, OCR_EMPTY_REASON, "ocr_empty", pages=transcribed.page_count
            )
            return None

        # How much the engine admitted it could not read. A transcription that is
        # mostly guesses cannot support a citation, so it is refused rather than
        # indexed — the same reasoning as FR-2, applied one step later.
        illegible_chars = sum(p.illegible for p in pages) * len(ILLEGIBLE)
        ratio = illegible_chars / max(transcribed.total_chars, 1)
        if ratio > self._settings.ocr_max_illegible_ratio:
            await self._fail(
                document_id,
                OCR_UNREADABLE_REASON.format(percent=round(ratio * 100)),
                "ocr_unreadable",
                pages=transcribed.page_count,
            )
            return None

        METRICS.incr("ocr", engine=self._ocr.name)
        METRICS.observe("ocr_illegible_ratio", ratio)
        log.info(
            "scan transcribed",
            extra={
                "document_id": document_id,
                "engine": self._ocr.name,
                "pages": transcribed.page_count,
                "chars": transcribed.total_chars,
                "illegible_ratio": round(ratio, 4),
            },
        )
        return transcribed

    async def _index(
        self, owner_id: str, document_id: str, result: ParseResult, *, text_source: str = "native"
    ) -> None:
        await self._documents.replace_pages(
            document_id, [(p.page, p.text, p.char_count) for p in result.pages]
        )

        document = await self._documents.get(owner_id, document_id)
        matter_id = document.matter_id if document else ""

        chunks = chunk_pages(
            result.pages,
            target_chars=self._settings.chunk_target_chars,
            overlap_chars=self._settings.chunk_overlap_chars,
        )
        await self._documents.replace_chunks(
            document_id,
            [
                (
                    f"ch-{uuid.uuid4().hex[:12]}",
                    document_id,
                    matter_id,
                    owner_id,
                    c.ordinal,
                    c.page_start,
                    c.page_end,
                    c.section,
                    ",".join(c.defined_terms) or None,
                    c.text,
                    c.char_count,
                    # Written as not-yet-indexed; the indexer below flips this once
                    # the vectors actually exist, so `/status` never reports an index
                    # that has not been built.
                    0,
                )
                for c in chunks
            ],
        )
        indexed = 0
        if self._index_vectors is not None and chunks:
            indexed = await self._index_vectors(document_id)

        await self._documents.mark_status(
            document_id,
            "ready",
            None,
            pages=result.page_count,
            token_estimate=result.total_chars // CHARS_PER_TOKEN,
            text_source=text_source,
        )
        log.info(
            "document indexed",
            extra={"document_id": document_id, "chunks": len(chunks), "indexed": indexed},
        )

    async def _bill_ocr(self, owner_id: str, pages: list) -> None:
        """One cost row for the document's transcription.

        Per document rather than per page: the pages of one scan are one unit of work
        to the reader of a cost table, and fifty rows for a fifty-page upload would
        bury the generation rows they sit beside. The per-page token counts are in the
        log if they are ever needed.

        Silent when the engine bills nothing — `tesseract` reports no tokens, and a
        $0 row reads as "this was free" rather than "no vendor was called".
        """
        if self._record_cost is None:
            return
        model = getattr(self._ocr, "model", "") or ""
        input_tokens = sum(p.input_tokens for p in pages)
        output_tokens = sum(p.output_tokens for p in pages)
        if not model or not (input_tokens or output_tokens):
            return
        try:
            await self._record_cost(
                owner_id=owner_id,
                purpose="ocr",
                model_id=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception:
            # A failed cost row must not fail an otherwise good transcription.
            log.exception("could not record ocr cost")

    async def _fail(
        self, document_id: str, reason: str, outcome: str, pages: int | None = None
    ) -> None:
        METRICS.incr("ingest", outcome=outcome)
        await self._documents.mark_status(document_id, "failed", reason, pages=pages)
        log.info("document rejected", extra={"document_id": document_id, "outcome": outcome})
