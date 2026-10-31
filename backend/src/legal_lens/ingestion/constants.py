"""Upload rules and the wording the user sees when one is broken."""

from __future__ import annotations

import re
from pathlib import Path

# FR-2. This string is rendered verbatim by the frontend's rejection notice, which
# carries the same text as its fallback — see RejectedScanNotice.tsx. The two must stay
# identical: a user told only "failed" re-uploads the same file, and a user told the
# wrong reason converts the wrong thing.
NO_TEXT_LAYER_REASON = (
    "This PDF has no extractable text layer — it appears to be a scan. Optical character "
    "recognition is not available in this release, so the document cannot be indexed or cited."
)

TOO_MANY_PAGES_REASON = (
    "This document has {pages} pages, over the {limit}-page limit for this release, "
    "so it was not indexed."
)

EMPTY_DOCUMENT_REASON = "This file contains no extractable text, so there is nothing to index."

# Distinct from UNREADABLE_REASON on purpose: that one blames the file, and this one
# does not, because an indexing bug is not something the user can fix by converting
# their document.
INTERNAL_FAILURE_REASON = (
    "Indexing failed because of an internal error, not a problem with this file. "
    "Re-upload to try again."
)

UNREADABLE_REASON = (
    "This file could not be read — it may be corrupt, encrypted or not the format its "
    "extension claims."
)

# Mirrors the client-side gate in DocumentsPage.tsx, which refuses anything else before
# a round trip. The server repeats the check because a client-side gate is a courtesy,
# not a control.
OCR_TOO_MANY_PAGES_REASON = (
    "This scan has {pages} pages, over the {limit}-page limit for optical character "
    "recognition, so it was not indexed."
)

OCR_UNREADABLE_REASON = (
    "This scan was too unclear to transcribe reliably — {percent}% of it could not be "
    "read. It was not indexed, because answers drawn from a partial transcription "
    "cannot be verified against the original."
)

OCR_EMPTY_REASON = (
    "Optical character recognition found no text in this scan. It may be a blank "
    "document or an image without writing."
)

ACCEPTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt"})

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}

# A light filename heuristic, used only to label the library. Nothing branches on it, so
# a wrong guess costs a mislabelled row rather than a wrong answer.
KIND_PATTERNS = (
    ("msa", re.compile(r"\b(msa|master[-_ ]?services?)\b", re.I)),
    ("nda", re.compile(r"\b(nda|non[-_ ]?disclosure|confidential(ity)?)\b", re.I)),
    ("complaint", re.compile(r"\b(complaint|petition|v\.?)\b", re.I)),
)


def suffix_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_accepted_filename(filename: str) -> bool:
    return suffix_of(filename) in ACCEPTED_SUFFIXES


def media_type_for(filename: str) -> str:
    return MEDIA_TYPES.get(suffix_of(filename), "application/octet-stream")


def detect_kind(filename: str) -> str:
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(filename):
            return kind
    return "other"


def title_from_filename(filename: str) -> str:
    """Mirrors the mock: drop the extension, turn separators into spaces."""
    stem = Path(filename).stem
    return re.sub(r"[-_]+", " ", stem).strip() or filename
