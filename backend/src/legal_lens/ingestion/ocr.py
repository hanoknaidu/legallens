"""Optical character recognition for scanned PDFs.

**Read this before enabling OCR.** FR-2 rejects scanned PDFs rather than OCR-ing
them, and the BRD gives the reason: *"the silent-failure mode is worse than a clear
rejection."* OCR does not remove that risk, it changes its shape — and for a product
whose central claim is that every assertion is traceable to a page, the new shape is
worse in one specific way:

**A citation to OCR'd text is a citation to a transcription, not to the document.**
The viewer highlights a cited span by finding it in the stored page text. If that text
came from OCR, the highlight still works and still looks authoritative, but what it
proves is that the OCR engine produced those characters — not that the contract
contains them.

That matters most with an LLM engine. Tesseract fails *visibly*: a bad scan produces
obvious garbage. A vision model fails *plausibly* — asked to transcribe a blurry
indemnity clause, it can emit well-formed contract language that was never on the
page. Confident, quotable, and wrong is the worst possible output here.

Two mitigations are built in and neither is optional:

1. Every engine's prompt or post-processing marks unreadable regions `[ILLEGIBLE]`
   rather than guessing, and the LLM prompt says so in several ways because this is
   the instruction most likely to be quietly ignored.
2. A document ingested by OCR is recorded with `text_source = 'ocr'`, so the API, the
   viewer and any citation drawn from it can say where the text came from. A page
   whose provenance is unmarked is the failure this field exists to prevent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, Protocol

log = logging.getLogger(__name__)

ILLEGIBLE = "[ILLEGIBLE]"

#: What a page with nothing on it must return. Rule 7 used to say "output nothing",
#: and a vision model shown a blank page reached for a *description* of the blank page
#: instead — "The image provided appears to be completely blank…" — which then
#: indexed as document text and became citable. Saying nothing is an unnatural
#: response shape for a chat model; saying one exact string is one it complies with.
NO_TEXT = "[NO TEXT]"

#: A page the model described rather than transcribed. Rules 1 and 7 forbid this and
#: it happens anyway, so it is caught here as well as asked for there — the prompt
#: requests, the code enforces.
#:
#: Guarded by length at the call site: narration is a sentence or two, while a real
#: page of a contract is hundreds of characters, so a clause that happens to discuss
#: "the image" cannot be mistaken for it.
_NARRATION = re.compile(
    r"\b(?:"
    r"(?:image|page|document|scan)\b[^.]{0,80}?\b(?:is|appears?|seems?|looks?)\b"
    r"[^.]{0,80}?\b(?:blank|empty|no\s+(?:visible|discernible|legible)?\s*text)"
    r"|no\s+(?:visible|discernible|legible)?\s*text\b[^.]{0,40}?\b(?:to\s+transcribe|content|present)"
    r"|nothing\s+to\s+transcribe"
    r"|(?:cannot|can't|unable\s+to)\s+(?:read|transcribe|make\s+out)"
    r"|there\s+is\s+no\s+text"
    r")",
    re.I,
)

#: Above this, a response is too long to be a one-line description of a blank page.
NARRATION_MAX_CHARS = 400

#: Haiku: transcription is mechanical and per-page, so this is the high-volume,
#: cost-sensitive call in the whole system.
DEFAULT_VISION_MODEL = "claude-haiku-4-5"

TRANSCRIBE_PROMPT = f"""Transcribe the text in this page image, exactly as it appears.

Rules:

1. Output only the transcribed text. No preamble, no commentary, no description of
   the page, no markdown fences.
2. Transcribe verbatim. Do not correct spelling, grammar, numbering or punctuation,
   and do not modernise archaic wording. If the page says "Supplier shall indemnifie",
   write that.
3. Preserve the reading order and the line and paragraph breaks. Keep clause numbers
   and headings on their own lines where they appear that way.
4. For a table, transcribe it row by row, separating cells with " | ".
5. **If any word, number or passage is illegible, write {ILLEGIBLE} in its place.**
   Do not infer it from context. Do not reconstruct what a contract of this type
   usually says. A gap marked {ILLEGIBLE} is useful; an invented clause is harmful,
   because it will be quoted back to a lawyer as though it were in their document.
6. If a number is partly readable, mark the whole number {ILLEGIBLE} rather than
   guessing a digit. A wrong figure in a fee or a liability cap is the most damaging
   error you can make here.
7. If the page contains no text at all — a blank page, or an image with no writing —
   output exactly {NO_TEXT} and nothing else. Do not describe the page, do not
   explain that it is blank, and do not apologise. A description of the page is not a
   transcription of it, and it will be stored and cited as though it were the
   document's own words.

You are transcribing a legal document that will be cited. Accuracy about what is
*actually there*, including admitting what you cannot read, matters more than
producing clean prose."""


@dataclass(frozen=True)
class OcrPage:
    page: int
    text: str
    #: Count of `[ILLEGIBLE]` markers, so the pipeline can judge transcription quality.
    illegible: int
    #: True when the model described the page instead of transcribing it. The text is
    #: emptied in that case, so this is the only remaining trace — and it is a signal
    #: about the transcription as a whole, not just this page: a model ignoring rule 1
    #: here was free to ignore rule 5 elsewhere.
    narrated: bool = False
    #: What this page's call cost. One call per page, so this is per-call (NFR-10).
    #: Zero for an engine that makes no vendor call.
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class OcrEngine(Protocol):
    name: str
    #: The model id for the cost row, or "" for an engine that bills nothing.
    model: str

    def transcribe(self, images: list[bytes]) -> list[OcrPage]: ...


def _count_illegible(text: str) -> int:
    return len(re.findall(re.escape(ILLEGIBLE), text))


def transcription_of(raw: str) -> tuple[str, bool]:
    """`(text, narrated)` — the page's text, with non-transcriptions removed.

    A page is stored as empty rather than as the model's prose about it. That is the
    honest representation: we have no text for this page. It also routes the failure
    into guards that already exist — an all-unreadable scan becomes `total_chars == 0`
    and is rejected with the reason that fits, instead of indexing commentary.

    Not a quality judgement on legible pages: anything long enough to be a real page,
    or not matching the narration shapes, is returned untouched.
    """
    text = raw.strip()
    if not text or text == NO_TEXT:
        return "", False
    if len(text) <= NARRATION_MAX_CHARS and _NARRATION.search(text):
        return "", True
    return raw, False


class TesseractOcr:
    """Local OCR. No vendor call, no new data processor, and fails visibly.

    Needs the `tesseract` binary on PATH (`brew install tesseract`), which is checked
    at construction so a missing install is a startup error rather than a failed
    upload.
    """

    name = "tesseract"
    #: No vendor call, so nothing to bill.
    model = ""

    def __init__(self, *, language: str = "eng") -> None:
        import shutil

        if shutil.which("tesseract") is None:
            raise RuntimeError(
                "ocr_engine='tesseract' needs the tesseract binary on PATH. "
                "Install it with `brew install tesseract`, or use ocr_engine='claude'."
            )
        try:
            import pytesseract  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "ocr_engine='tesseract' needs the 'ocr' extra: uv sync --extra ocr"
            ) from exc
        self._language = language

    def transcribe(self, images: list[bytes]) -> list[OcrPage]:
        import io

        import pytesseract
        from PIL import Image

        pages: list[OcrPage] = []
        for index, png in enumerate(images, start=1):
            with Image.open(io.BytesIO(png)) as image:
                raw = pytesseract.image_to_string(image, lang=self._language)
            # Tesseract does not narrate — it returns noise or nothing — but the
            # normalisation is applied here too so one function defines what counts
            # as a transcription, whichever engine produced it.
            text, narrated = transcription_of(raw)
            pages.append(
                OcrPage(
                    page=index,
                    text=text,
                    illegible=_count_illegible(text),
                    narrated=narrated,
                )
            )
        return pages


class ClaudeVisionOcr:
    """Transcription by a vision model.

    Chosen as the default because it needs no system binary and adds no new processor
    of privileged text — the same vendor already receives document text for
    generation, so the §8.2 data-processing question is already settled for it.

    The tradeoff is the one in the module docstring: this engine can fail plausibly.
    The prompt works hard against that and `illegible` counts how often it admitted a
    gap, which is the signal that it was being honest.
    """

    name = "claude-vision"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_VISION_MODEL,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "ocr_engine='claude' needs an API key: set ANTHROPIC_API_KEY in "
                "backend/.env (or LEGAL_LENS_ANTHROPIC_API_KEY to override it for "
                "this service only). Or use ocr_engine='tesseract', which makes no "
                "vendor call."
            )
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, default_headers=headers or None)
        self._model = model
        #: Read by the pipeline for the cost row.
        self.model = model

    def transcribe(self, images: list[bytes]) -> list[OcrPage]:
        import base64

        pages: list[OcrPage] = []
        for index, png in enumerate(images, start=1):
            # One request per page rather than one for the document: a page is the
            # citable unit, and batching them invites the model to merge or reorder
            # pages, which would put a cited span on the wrong page.
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(png).decode(),
                                },
                            },
                            {"type": "text", "text": TRANSCRIBE_PROMPT},
                        ],
                    }
                ],
            )
            usage_in = response.usage.input_tokens if response.usage else 0
            usage_out = response.usage.output_tokens if response.usage else 0

            if response.stop_reason == "refusal":
                log.warning("ocr refused", extra={"page": index})
                pages.append(
                    OcrPage(
                        page=index,
                        text="",
                        illegible=0,
                        input_tokens=usage_in,
                        output_tokens=usage_out,
                    )
                )
                continue

            raw = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
            # Rules 1 and 7 forbid describing the page. When the model does it anyway,
            # the description is discarded here rather than stored as the document's
            # own words — see `transcription_of`.
            text, narrated = transcription_of(raw)
            if narrated:
                log.warning(
                    "ocr described the page instead of transcribing it; discarded",
                    extra={"page": index, "returned": raw.strip()[:160]},
                )
            pages.append(
                OcrPage(
                    page=index,
                    text=text,
                    illegible=_count_illegible(text),
                    narrated=narrated,
                    input_tokens=usage_in,
                    output_tokens=usage_out,
                )
            )
            log.info(
                "ocr page transcribed",
                extra={
                    "page": index,
                    "chars": len(text),
                    "illegible": _count_illegible(text),
                    "narrated": narrated,
                    "input_tokens": usage_in,
                    "output_tokens": usage_out,
                },
            )
        return pages


EngineName = Literal["none", "tesseract", "claude"]


def engine_for(
    name: EngineName,
    *,
    api_key: str | None = None,
    headers: dict[str, str] | None = None,
) -> OcrEngine | None:
    """Build the configured engine, or None when OCR is off.

    Raises rather than falling back: a misconfigured engine that silently became
    "no OCR" would look like FR-2 rejection and be diagnosed as a parser bug.
    """
    if name == "none":
        return None
    if name == "tesseract":
        return TesseractOcr()
    if name == "claude":
        return ClaudeVisionOcr(api_key=api_key or "", headers=headers)
    raise ValueError(f"unknown ocr_engine: {name!r}")
