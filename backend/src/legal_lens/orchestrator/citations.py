"""Turning pages into document blocks, and citations back into pages.

The decision this file exists for: **each document is sent as a custom-content
document whose content blocks are its pages.** Citations then come back as
`content_block_location` carrying `start_block_index`, which maps onto a page number
by position — so the page a citation names is computed by the API from the text it
actually read, not reported by a model that was asked to produce one. Asking a model
to write page numbers into its prose is the standard way this feature ends up citing
the wrong page confidently.

`cited_text` comes back verbatim from the blocks we supplied, which is what the viewer
highlights with (FR-7, FR-8).

Two API constraints are load-bearing here:

- `citations: {"enabled": true}` is all-or-nothing across the document blocks in a
  request. A mix is rejected.
- Citations are **incompatible with `output_config.format`** — sending both is a 400.
  That is why the summary workflow parses `## Heading` prose instead of requesting a
  JSON schema, and why no workflow in this package uses structured outputs.
"""

from __future__ import annotations

import logging
from typing import Any

from .types import Citation, Page

log = logging.getLogger(__name__)


def build_documents(pages: list[Page]) -> tuple[list[dict[str, Any]], list[list[Page]]]:
    """Group pages into one custom-content document per source document.

    Returns the content blocks and a parallel index: `index[document_index]` is that
    document's pages in block order, so a citation's `start_block_index` resolves to a
    real page without trusting the model to report one.

    Page order within a document, and document order within the list, are both
    preserved exactly as given. That ordering is not cosmetic — it *is* the citation
    mapping, so a selection that returned pages in a different order on an identical
    request would make the same citation name a different page.
    """
    ordered: list[str] = []
    grouped: dict[str, list[Page]] = {}
    for page in pages:
        if page.doc_id not in grouped:
            grouped[page.doc_id] = []
            ordered.append(page.doc_id)
        grouped[page.doc_id].append(page)

    blocks: list[dict[str, Any]] = []
    index: list[list[Page]] = []
    for doc_id in ordered:
        doc_pages = grouped[doc_id]
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "content",
                    "content": [{"type": "text", "text": p.text} for p in doc_pages],
                },
                "title": doc_pages[0].title,
                "citations": {"enabled": True},
            }
        )
        index.append(doc_pages)
    return blocks, index


def cache_last(blocks: list[dict[str, Any]], *, enabled: bool = True) -> list[dict[str, Any]]:
    """Put a cache breakpoint after the last document block, if it will be read.

    **A cache write is not free, and this is where the bill is.** A write costs 1.25x
    the input rate and a read costs 0.1x, so caching a prefix that is never read again
    is a 25% surcharge on the largest part of the request. Measured on a five-turn
    run: cache writes were **62.6%** of the total spend — the same ~20K-token document
    prefix, written once per workflow, and two of those three writes were read zero
    times.

    So the breakpoint is a decision per workflow, not a default:

    - **`ask` — worth it.** A conversation asks several questions of the same corpus,
      and under the whole-document route the prefix is byte-identical between turns.
      Measured: one 19,955-token write served 39,910 tokens of reads.
    - **`summary` and `draft` — not worth it.** Each produces one artifact from one
      call. There is no second request with the same prefix to read it, so the write
      is pure surcharge.

    Caching is prefix-matched in the order `tools` → `system` → `messages`, and the
    system prompt differs per workflow — which is why these three prefixes cannot
    share one entry however stable the documents are. Check
    `usage.cache_read_input_tokens`: if it is zero across repeated calls, something
    earlier in the prefix is varying.
    """
    if not blocks or not enabled:
        return blocks
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return blocks


def resolve(raw: Any, index: list[list[Page]], n: int) -> Citation | None:
    """Turn one API citation into ours, resolving its block index to a real page.

    Returns None rather than guessing. A dropped chip is a visible absence; a chip
    pointing at the wrong page is a claim the user has no way to check.
    """
    document_index = getattr(raw, "document_index", None)
    cited_text = getattr(raw, "cited_text", None)
    if document_index is None or not cited_text:
        return None
    if not 0 <= document_index < len(index):
        log.warning("citation document_index out of range", extra={"index": document_index})
        return None

    pages = index[document_index]
    block = getattr(raw, "start_block_index", None)
    if block is None or not 0 <= block < len(pages):
        # A `char_location` or `page_location` citation lands here, which happens only
        # if a document was sent as plain text or a PDF rather than as custom content.
        log.warning(
            "citation block index unusable",
            extra={
                "block": block,
                "pages": len(pages),
                "type": getattr(raw, "type", None),
            },
        )
        return None

    page = pages[block]
    return Citation(
        n=n,
        doc_id=page.doc_id,
        doc_title=page.title,
        page=page.page,
        cited_text=cited_text,
    )


def citation_marker(text_so_far: str, n: int) -> str:
    """The `{{cite:n}}` marker to append after the span this citation annotates.

    The chat renderer turns these into chips (`frontend/src/components/chat/
    Markdown.tsx`), and they are the only thing that ties a particular claim to a
    particular page — the "N sources" badge counts them but points nowhere. The
    model is *not* asked to write them: every prompt in this package forbids it,
    because a model writing its own citation numbers gets them wrong. They are
    injected here instead, where the API has already told us which page the span
    came from.

    Placement: the API sends a citation at the end of the block it annotates, so
    appending puts the marker directly after the sentence it supports.

    Attached with no space, which is the convention the frontend was built against —
    see `frontend/src/mocks/fixtures/messages.ts`, where every marker follows its
    sentence's full stop directly. The chip carries its own horizontal margin, so a
    space here would render as a gap before the number. `text_so_far` is taken for
    the signature rather than the spacing it no longer decides, because the one thing
    a future change here will need is what came before.
    """
    del text_so_far
    return f"{{{{cite:{n}}}}}"


class CitationCollector:
    """Accumulates citations across a stream, dropping repeats.

    The model can cite the same span twice; the chip list should not show it twice.
    """

    def __init__(self) -> None:
        self.citations: list[Citation] = []
        self._by_key: dict[tuple[str, int, str], Citation] = {}

    def observe(self, raw: Any, index: list[list[Page]]) -> tuple[Citation | None, bool]:
        """Resolve one raw citation to `(citation, is_new)`.

        Two different questions get two different answers here, which is why this
        exists alongside `add`:

        - *Is this a new source?* decides whether a chip joins the list.
        - *Which number does this sentence point at?* decides what marker goes in the
          prose, and a span the model cited twice needs a marker in both places even
          though it is one source.

        Collapsing them — returning None for a repeat, as `add` does — drops the
        second marker and leaves that sentence with no way back to its page.
        """
        resolved = resolve(raw, index, len(self.citations) + 1)
        if resolved is None:
            return None, False
        key = (resolved.doc_id, resolved.page, resolved.cited_text)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing, False
        self._by_key[key] = resolved
        self.citations.append(resolved)
        return resolved, True

    def add(self, raw: Any, index: list[list[Page]]) -> Citation | None:
        """Returns the citation if it is new, None if unusable or already present."""
        citation, is_new = self.observe(raw, index)
        return citation if is_new else None
