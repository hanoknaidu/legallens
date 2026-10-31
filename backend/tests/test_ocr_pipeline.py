"""The FR-2 fork: a scanned PDF is rejected, or transcribed and indexed.

Tested with a deterministic fake engine rather than tesseract or a vendor call, so
the branch logic is covered without a binary install or a bill. The engines
themselves are thin — rasterise, call, count markers — and what has to be right is
the surrounding policy: when OCR runs, when it refuses, and what provenance the
resulting document carries.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from fixtures import scanned_pdf, text_pdf
from helpers import await_status
from legal_lens.config import Settings
from legal_lens.ingestion.ocr import (
    ILLEGIBLE,
    NARRATION_MAX_CHARS,
    NO_TEXT,
    TRANSCRIBE_PROMPT,
    OcrPage,
    transcription_of,
)

SCAN_TEXT = [
    "MASTER SERVICES AGREEMENT between Northwind Logistics and Arbor Systems.",
    "Section 7.2 Indemnification. The Supplier shall indemnify the Customer against "
    "all third-party claims arising from the Services.",
]


class FakeOcr:
    """Returns fixed text per page, so assertions are about policy not accuracy."""

    name = "fake"

    def __init__(self, pages: list[str] | None = None) -> None:
        self._pages = pages if pages is not None else SCAN_TEXT
        self.calls = 0

    def transcribe(self, images: list[bytes]) -> list[OcrPage]:
        self.calls += 1
        out: list[OcrPage] = []
        for index in range(len(images)):
            text = self._pages[index] if index < len(self._pages) else ""
            out.append(OcrPage(page=index + 1, text=text, illegible=text.count(ILLEGIBLE)))
        return out


@pytest.fixture
def ocr_settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path,
        llm_provider="echo",
        ocr_enabled=True,
        ocr_engine="none",  # the engine is injected by the test
        ocr_dpi=72,  # fast; the fake engine ignores the image anyway
    )


async def _client_with_ocr(settings: Settings, engine):
    """An app whose pipeline uses the supplied engine."""
    from httpx import ASGITransport

    from legal_lens.main import create_app

    app = create_app(settings)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test/api")
    return app, client


# ---------------------------------------------------------------------------
# Default behaviour: FR-2 is unchanged
# ---------------------------------------------------------------------------


async def test_without_ocr_a_scan_is_still_rejected(client: AsyncClient) -> None:
    """The default `client` fixture has OCR off, which is the shipped default."""
    created = (
        await client.post(
            "/documents", files={"file": ("scan.pdf", scanned_pdf(3), "application/pdf")}
        )
    ).json()
    final = await await_status(client, created["id"], "failed")

    # FR-2, untouched: the BRD's decision survives the addition of OCR.
    assert final["failureReason"].startswith("This PDF has no extractable text layer")
    assert final["textSource"] == "native"


async def test_ocr_is_reported_as_disabled_on_health(client: AsyncClient) -> None:
    checks = (await client.get("/health")).json()["checks"]
    # "Why was my scan rejected" needs a visible answer.
    assert "disabled" in checks["ocr"]
    assert checks["ocr"].startswith("ok")


# ---------------------------------------------------------------------------
# With OCR configured
# ---------------------------------------------------------------------------


async def test_a_scan_is_transcribed_and_indexed(ocr_settings: Settings) -> None:
    engine = FakeOcr()
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents",
                files={"file": ("contract-scan.pdf", scanned_pdf(2), "application/pdf")},
            )
        ).json()
        final = await await_status(client, created["id"], "ready")

        assert engine.calls == 1
        assert final["status"] == "ready"
        # The provenance is recorded, because a citation into this text quotes a
        # transcription rather than the document.
        assert final["textSource"] == "ocr"

        # And the recovered text is on the page it was transcribed from, so the
        # citation path works exactly as it does for a native text layer.
        page = (await client.get(f"/documents/{created['id']}/page/2")).json()
        assert "shall indemnify the Customer" in page["text"]


async def test_transcribed_text_is_chunked_and_indexed_like_any_other(
    ocr_settings: Settings,
) -> None:
    engine = FakeOcr()
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents", files={"file": ("s.pdf", scanned_pdf(2), "application/pdf")}
            )
        ).json()
        final = await await_status(client, created["id"], "ready")

        # The second branch rejoins the first: same chunking, same embedding, same
        # index. If this ever diverges, scans become unsearchable while looking ready.
        assert final["chunksIndexed"] >= 1


async def test_a_native_pdf_never_reaches_ocr(ocr_settings: Settings) -> None:
    engine = FakeOcr()
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents",
                files={"file": ("native.pdf", text_pdf(SCAN_TEXT), "application/pdf")},
            )
        ).json()
        final = await await_status(client, created["id"], "ready")

        # Transcribing a document that already has a text layer would replace exact
        # text with a guess, and cost a call per page to do it.
        assert engine.calls == 0
        assert final["textSource"] == "native"


# ---------------------------------------------------------------------------
# When OCR should refuse
# ---------------------------------------------------------------------------


async def test_a_mostly_illegible_scan_is_rejected(ocr_settings: Settings) -> None:
    # Over the 25% default: a transcription this uncertain cannot support a citation.
    engine = FakeOcr([f"The {ILLEGIBLE} shall {ILLEGIBLE} the {ILLEGIBLE}.", ""])
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents", files={"file": ("blurry.pdf", scanned_pdf(2), "application/pdf")}
            )
        ).json()
        final = await await_status(client, created["id"], "failed")

        assert "too unclear to transcribe" in final["failureReason"]
        # The reason names the figure, so the user can judge whether a better scan
        # would help.
        assert "%" in final["failureReason"]


async def test_a_scan_with_no_recoverable_text_is_rejected(ocr_settings: Settings) -> None:
    engine = FakeOcr(["", ""])
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents", files={"file": ("blank.pdf", scanned_pdf(2), "application/pdf")}
            )
        ).json()
        final = await await_status(client, created["id"], "failed")

        assert "found no text" in final["failureReason"]


async def test_an_oversized_scan_is_refused_before_any_call(tmp_path) -> None:
    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        llm_provider="echo",
        ocr_enabled=True,
        ocr_engine="none",
        ocr_dpi=72,
        ocr_max_pages=2,
    )
    engine = FakeOcr()
    app, client = await _client_with_ocr(settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ingestion._ocr = engine
        created = (
            await client.post(
                "/documents", files={"file": ("big.pdf", scanned_pdf(5), "application/pdf")}
            )
        ).json()
        final = await await_status(client, created["id"], "failed")

        # Refused before rasterising or calling, so the largest scans cannot become
        # the most expensive uploads in the system.
        assert engine.calls == 0
        assert "over the 2-page limit" in final["failureReason"]


async def test_health_names_the_engine_when_ocr_is_on(ocr_settings: Settings) -> None:
    engine = FakeOcr()
    app, client = await _client_with_ocr(ocr_settings, engine)

    async with client, app.router.lifespan_context(app):
        app.state.services.ocr = engine
        checks = (await client.get("/health")).json()["checks"]
        assert checks["ocr"] == "ok (fake)"


# ---------------------------------------------------------------------------
# The rasteriser
# ---------------------------------------------------------------------------


def test_the_rasteriser_renders_each_page_to_png() -> None:
    from legal_lens.ingestion.rasterize import page_count, page_images

    data = scanned_pdf(3)
    assert page_count(data) == 3

    images = page_images(data, dpi=72)
    assert len(images) == 3
    # PNG magic, so a silent format change is caught rather than reaching the engine.
    for png in images:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_rasteriser_honours_the_page_cap() -> None:
    from legal_lens.ingestion.rasterize import page_images

    assert len(page_images(scanned_pdf(6), dpi=72, max_pages=2)) == 2


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


def test_ocr_off_builds_no_engine() -> None:
    from legal_lens.ingestion.ocr import engine_for

    assert engine_for("none") is None


def test_an_unknown_engine_raises_rather_than_silently_disabling() -> None:
    from legal_lens.ingestion.ocr import engine_for

    # A misconfiguration that fell through to None would look exactly like FR-2
    # rejection and be diagnosed as a parser bug.
    with pytest.raises(ValueError, match="unknown ocr_engine"):
        engine_for("magic")  # type: ignore[arg-type]


def test_the_claude_engine_requires_a_key() -> None:
    from legal_lens.ingestion.ocr import engine_for

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        engine_for("claude", api_key="")


def test_the_missing_key_error_names_both_variables_and_the_alternative() -> None:
    from legal_lens.ingestion.ocr import engine_for

    # The message a stuck operator reads. It has to name the variable they will
    # actually set, and the engine that needs no key at all.
    with pytest.raises(RuntimeError) as caught:
        engine_for("claude", api_key="")
    message = str(caught.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "LEGAL_LENS_ANTHROPIC_API_KEY" in message
    assert "tesseract" in message


def test_the_transcription_prompt_forbids_guessing() -> None:
    from legal_lens.ingestion.ocr import ILLEGIBLE, TRANSCRIBE_PROMPT

    # The instruction most likely to be quietly ignored, and the one that matters:
    # a vision model asked to read a blurry indemnity can emit well-formed contract
    # language that was never on the page.
    assert ILLEGIBLE in TRANSCRIBE_PROMPT
    assert "Do not infer it from context" in TRANSCRIBE_PROMPT
    assert "usually says" in TRANSCRIBE_PROMPT
    assert "verbatim" in TRANSCRIBE_PROMPT.lower()


# ---------------------------------------------------------------------------
# Enabled but unavailable — the state that used to take the whole service down
# ---------------------------------------------------------------------------


async def test_ocr_enabled_without_a_key_degrades_instead_of_crashing(tmp_path) -> None:
    """Following the setup instructions half-way must not be an outage.

    Setting `ocr_enabled=true` and forgetting the key used to raise inside the
    lifespan, so uvicorn logged "Application startup failed. Exiting." and the entire
    service — every read path, every native-text upload, every query — was down
    because an optional ingestion feature lacked a credential.
    """
    from httpx import ASGITransport

    from legal_lens.main import create_app

    settings = Settings(
        environment="test",
        data_dir=tmp_path,
        llm_provider="echo",
        ocr_enabled=True,
        ocr_engine="claude",
        anthropic_api_key=None,
    )
    app = create_app(settings)

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api") as client,
        app.router.lifespan_context(app),
    ):
        health = (await client.get("/health")).json()

        # It boots.
        assert health["status"] == "degraded"
        # And says precisely what is wrong, because "enabled but unavailable" and
        # "switched off" produce the same FR-2 rejection and are otherwise
        # indistinguishable from the outside.
        assert "enabled but unavailable" in health["checks"]["ocr"]
        assert "ANTHROPIC_API_KEY" in health["checks"]["ocr"]

        # Native-text ingestion still works: the degradation is confined to scans.
        files = {"file": ("native.pdf", text_pdf(SCAN_TEXT), "application/pdf")}
        created = (await client.post("/documents", files=files)).json()
        final = await await_status(client, created["id"], "ready")
        assert final["textSource"] == "native"


async def test_the_three_ocr_health_states_are_distinguishable(tmp_path) -> None:
    from legal_lens.api.platform import _ocr_state

    class _Services:
        def __init__(self, ocr, reason=None):
            self.ocr = ocr
            self.ocr_unavailable_reason = reason

    off = Settings(environment="test", data_dir=tmp_path, ocr_enabled=False)
    on = Settings(environment="test", data_dir=tmp_path, ocr_enabled=True)

    assert "disabled" in _ocr_state(_Services(None), off)
    assert _ocr_state(_Services(FakeOcr()), on) == "ok (fake)"
    assert "enabled but unavailable: no key" in _ocr_state(_Services(None, "no key"), on)


def test_a_bad_engine_name_is_rejected_by_settings(tmp_path) -> None:
    from pydantic import ValidationError

    # Caught at settings construction rather than at engine build time, which is
    # earlier and better: a deployment must not run with a typo'd setting ignored.
    with pytest.raises(ValidationError):
        Settings(environment="test", data_dir=tmp_path, ocr_engine="magic")


# ---------------------------------------------------------------------------
# A vision model that describes the page instead of transcribing it
#
# Found by running the real engine against a scan with nothing legible on it. The
# prompt forbids this in two separate rules and Haiku did it anyway, returning "The
# image provided appears to be completely blank…" — which passed both guards (145
# chars, so not empty; no `[ILLEGIBLE]` markers, so a 0.00 illegible ratio) and was
# stored, chunked and indexed as the document's own words.
# ---------------------------------------------------------------------------

NARRATIONS = [
    "The image provided appears to be completely blank or contains only a uniform "
    "gray background with no visible text or other content to transcribe.",
    "The image appears to be a blank or nearly blank gray page with no visible text "
    "content to transcribe.",
    "This page is blank — there is no text to transcribe.",
    "I cannot read any text in this image.",
    "There is no text on this page.",
]


class RealisticOcr:
    """A fake that normalises its output the way a real engine does.

    `FakeOcr` builds `OcrPage` directly, which is right for testing policy on known
    text. This one passes its raw output through `transcription_of` exactly as
    `ClaudeVisionOcr` does, so the narration guard is exercised rather than bypassed.
    """

    name = "realistic"
    model = "claude-haiku-4-5"

    def __init__(self, raw_pages: list[str], *, tokens: tuple[int, int] = (1500, 40)) -> None:
        self._raw = raw_pages
        self._tokens = tokens
        self.calls = 0

    def transcribe(self, images: list[bytes]) -> list[OcrPage]:
        self.calls += 1
        out: list[OcrPage] = []
        for index in range(len(images)):
            raw = self._raw[index] if index < len(self._raw) else ""
            text, narrated = transcription_of(raw)
            out.append(
                OcrPage(
                    page=index + 1,
                    text=text,
                    illegible=text.count(ILLEGIBLE),
                    narrated=narrated,
                    input_tokens=self._tokens[0],
                    output_tokens=self._tokens[1],
                )
            )
        return out


@pytest.mark.parametrize("narration", NARRATIONS)
def test_a_described_page_is_not_a_transcription(narration: str) -> None:
    text, narrated = transcription_of(narration)
    assert text == ""
    assert narrated is True


def test_the_blank_page_sentinel_is_an_empty_page_not_a_failure() -> None:
    """A genuinely blank page is normal in a scan, and is not the model misbehaving."""
    text, narrated = transcription_of(NO_TEXT)
    assert text == ""
    assert narrated is False


def test_real_contract_text_is_returned_untouched() -> None:
    page = (
        "7. INDEMNIFICATION\n\n7.1 By Supplier. Supplier shall defend Customer "
        "against any claim brought by a third party."
    )
    assert transcription_of(page) == (page, False)


def test_a_clause_that_mentions_an_image_is_not_mistaken_for_narration() -> None:
    """The guard is bounded by length so a document cannot talk its way out of being one."""
    clause = (
        "12.4 Notices. The Supplier shall provide an image of the signed page upon "
        "request. No text message shall constitute notice, and the page is deemed "
        "delivered when received. " + "The parties further agree as follows. " * 8
    )
    assert len(clause) > NARRATION_MAX_CHARS
    assert transcription_of(clause) == (clause, False)


def test_the_prompt_demands_the_sentinel_rather_than_silence() -> None:
    """Rule 7 asks for an exact string because "output nothing" is what failed.

    A chat model shown a blank page reaches for a description; given one exact thing
    to say instead, it says it.
    """
    # Collapsed, because the prompt is line-wrapped and these phrases straddle breaks.
    flat = " ".join(TRANSCRIBE_PROMPT.split())
    assert NO_TEXT in flat
    assert "output exactly [NO TEXT] and nothing else" in flat
    assert "no commentary" in flat
    assert "Do not describe the page" in flat
    assert "A description of the page is not a transcription of it" in flat


async def test_a_fully_described_scan_is_rejected(ocr_settings: Settings) -> None:
    """The bug: this reached `ready` and became citable."""
    engine = RealisticOcr(NARRATIONS[:2])
    app, client = await _client_with_ocr(ocr_settings, engine)
    async with client:
        async with app.router.lifespan_context(app):
            app.state.services.ingestion._ocr = engine
            created = (
                await client.post(
                    "/documents",
                    files={"file": ("blank.pdf", scanned_pdf(2), "application/pdf")},
                )
            ).json()
            body = await await_status(client, created["id"], "failed")

            assert engine.calls == 1
            # Rejected with the reason that fits, not "enable OCR" — OCR *was* on.
            assert "no text" in (body["failureReason"] or "").lower()
            # And nothing was stored: no page text, so nothing to cite.
            page = await client.get(f"/documents/{created['id']}/page/1")
            assert page.status_code == 404 or not page.json().get("text", "").strip()


async def test_a_partly_described_scan_keeps_only_what_was_read(
    ocr_settings: Settings,
) -> None:
    """One real page and one described page: index the real one, store nothing for the other."""
    engine = RealisticOcr([SCAN_TEXT[1], NARRATIONS[0]])
    app, client = await _client_with_ocr(ocr_settings, engine)
    async with client:
        async with app.router.lifespan_context(app):
            app.state.services.ingestion._ocr = engine
            created = (
                await client.post(
                    "/documents",
                    files={"file": ("half.pdf", scanned_pdf(2), "application/pdf")},
                )
            ).json()
            await await_status(client, created["id"], "ready")

            first = (await client.get(f"/documents/{created['id']}/page/1")).json()["text"]
            second = (await client.get(f"/documents/{created['id']}/page/2")).json()["text"]
            assert "indemnify" in first.lower()
            # The description is gone rather than stored as the page's words.
            assert second.strip() == ""
            assert "appears to be" not in second


# ---------------------------------------------------------------------------
# OCR spend (NFR-10)
# ---------------------------------------------------------------------------


async def test_transcription_is_costed(ocr_settings: Settings) -> None:
    """OCR was the one model call in the system writing no cost row."""
    engine = RealisticOcr(SCAN_TEXT, tokens=(1500, 40))
    app, client = await _client_with_ocr(ocr_settings, engine)
    async with client:
        async with app.router.lifespan_context(app):
            services = app.state.services
            services.ingestion._ocr = engine
            created = (
                await client.post(
                    "/documents",
                    files={"file": ("scan.pdf", scanned_pdf(2), "application/pdf")},
                )
            ).json()
            await await_status(client, created["id"], "ready")

            rows = await services.db.fetch_all(
                "SELECT purpose, model_id, input_tokens, output_tokens, audit_id "
                "FROM usage_costs WHERE purpose = 'ocr'"
            )
            assert len(rows) == 1, "one row per document, not per page"
            assert rows[0]["model_id"] == "claude-haiku-4-5"
            # Summed across the two pages — one call each.
            assert rows[0]["input_tokens"] == 3000
            assert rows[0]["output_tokens"] == 80
            # Ingestion has no assistant turn to hang the cost off.
            assert rows[0]["audit_id"] is None


async def test_a_rejected_scan_is_still_costed(ocr_settings: Settings) -> None:
    """The calls were made and billed; a silent rejection hides that."""
    engine = RealisticOcr(NARRATIONS[:2])
    app, client = await _client_with_ocr(ocr_settings, engine)
    async with client:
        async with app.router.lifespan_context(app):
            services = app.state.services
            services.ingestion._ocr = engine
            created = (
                await client.post(
                    "/documents",
                    files={"file": ("blank.pdf", scanned_pdf(2), "application/pdf")},
                )
            ).json()
            await await_status(client, created["id"], "failed")

            rows = await services.db.fetch_all(
                "SELECT purpose FROM usage_costs WHERE purpose = 'ocr'"
            )
            assert len(rows) == 1


async def test_an_engine_that_bills_nothing_writes_no_row(ocr_settings: Settings) -> None:
    """`tesseract` makes no vendor call, and a $0 row reads as "this was free"."""
    engine = FakeOcr()  # no `model`, no token counts
    app, client = await _client_with_ocr(ocr_settings, engine)
    async with client:
        async with app.router.lifespan_context(app):
            services = app.state.services
            services.ingestion._ocr = engine
            created = (
                await client.post(
                    "/documents",
                    files={"file": ("scan.pdf", scanned_pdf(2), "application/pdf")},
                )
            ).json()
            await await_status(client, created["id"], "ready")

            rows = await services.db.fetch_all("SELECT purpose FROM usage_costs")
            assert rows == []
