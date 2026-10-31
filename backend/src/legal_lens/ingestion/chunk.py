"""Pages → retrieval chunks.

Chunks carry the page range they came from, because a citation has to name a page and
a chunk that lost its provenance can only be cited as "somewhere in the document".
They overlap, so a clause split across a boundary survives in at least one chunk whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import ParsedPage

# "Section 7.2", "7.2 Indemnification", "ARTICLE IV" — a boundary worth preferring when
# one falls near the target size, so a chunk tends to begin where a clause begins.
SECTION_HEADING = re.compile(
    r"^\s*(?:ARTICLE\s+[IVXLC\d]+|SECTION\s+[\d.]+|\d+(?:\.\d+)*)\s*[.\-—:]?\s+\S",
    re.MULTILINE,
)

# Defined terms, as contracts introduce them: `"Confidential Information" means …`.
DEFINED_TERM = re.compile(r'[""“”]([A-Z][A-Za-z \-]{2,60})[""“”]\s+(?:means|shall mean)')


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    page_start: int
    page_end: int
    section: str | None
    defined_terms: list[str]

    @property
    def char_count(self) -> int:
        return len(self.text)


def _current_section(text: str) -> str | None:
    matches = SECTION_HEADING.findall(text)
    if not matches:
        return None
    return " ".join(str(matches[0]).split())[:120] or None


def chunk_pages(pages: list[ParsedPage], *, target_chars: int, overlap_chars: int) -> list[Chunk]:
    """Walk the document as one stream, emitting chunks at page granularity.

    Pages are the unit rather than characters because `page_start`/`page_end` have to
    be true: a chunk assembled from character offsets could not say which page a span
    came from without recomputing the boundaries it just discarded.
    """
    usable = [p for p in pages if p.text.strip()]
    if not usable:
        return []

    chunks: list[Chunk] = []
    buffer: list[ParsedPage] = []
    size = 0

    def flush(carry: list[ParsedPage]) -> list[ParsedPage]:
        if not buffer:
            return []
        text = "\n\n".join(p.text for p in buffer)
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                text=text,
                page_start=buffer[0].page,
                page_end=buffer[-1].page,
                section=_current_section(text),
                # Recorded rather than resolved: a chunk that uses a term defined
                # elsewhere retrieves confidently and wrongly, and knowing which terms
                # appear is what lets a later pass carry the definitions forward.
                defined_terms=sorted({m.strip() for m in DEFINED_TERM.findall(text)}),
            )
        )
        return carry

    for page in usable:
        buffer.append(page)
        size += page.char_count

        if size >= target_chars:
            # Overlap by trailing pages rather than trailing characters, so the next
            # chunk's `page_start` stays a page the text genuinely appears on.
            carry: list[ParsedPage] = []
            carried = 0
            for candidate in reversed(buffer):
                if carried >= overlap_chars or len(carry) >= len(buffer) - 1:
                    break
                carry.insert(0, candidate)
                carried += candidate.char_count
            buffer = flush(carry)
            size = sum(p.char_count for p in buffer)

    if buffer:
        # A final chunk that only repeats the previous one's overlap adds nothing but a
        # duplicate retrieval hit.
        if not chunks or buffer[-1].page > chunks[-1].page_end:
            flush([])

    return chunks
