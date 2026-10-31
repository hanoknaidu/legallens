"""The grounded streaming call all three workflows make.

One place, because the parts that are easy to get subtly wrong are identical across
`ask`, `summary` and `draft`: assembling custom-content document blocks, resolving
citations back to pages, holding back the abstention sentinel, and reading usage off
the final message.

The raw `anthropic` SDK is used rather than LangChain. Citations need control over
individual content blocks and over the `citations_delta` events that come back, and
that is exactly the layer a chat abstraction removes.

## Model constraints encoded here

These are 400s, not preferences:

- **Citations are incompatible with `output_config.format`.** No workflow in this
  package uses structured outputs; the summary parses `## Heading` prose instead.
- **`thinking: {"type": "disabled"}` is rejected by Sonnet 5.5**, and thinking cannot
  be disabled on Opus 5.5 at any effort. So the `thinking` parameter is never sent at
  all — depth is controlled only through `output_config.effort`.
- **`budget_tokens` is rejected** by both models.
- **Opus 5.5 defaults to `medium` effort**, so every call sets effort explicitly
  rather than relying on a default that differs by model.
- No forced `tool_choice` and no assistant prefill: both are 400s on these models.

Everything is streamed. These requests carry whole contracts at high `max_tokens`, and
a non-streaming call at that size risks the SDK's HTTP timeout.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..citations import CitationCollector, build_documents, cache_last, citation_marker
from ..types import Citation, Page, Usage

log = logging.getLogger(__name__)

#: The model says exactly this when the documents do not answer the question. A
#: sentinel rather than a prose judgement, because "does this answer read like a
#: refusal?" is not a question the application can reliably answer after the fact.
ABSTAIN_SENTINEL = "NOT_IN_DOCUMENTS"
# ---------------------------------------------------------------------------
# Stream events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class CitationFound:
    citation: Citation


@dataclass(frozen=True)
class Abstained:
    """The documents do not support an answer. A correct outcome (FR-9), not an error."""

    message: str


@dataclass
class Completed:
    """The whole turn, once the stream has finished."""

    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    abstained: bool = False
    refused: bool = False


GenerationEvent = TextDelta | CitationFound | Abstained | Completed


ABSTENTION_MESSAGE = (
    "The documents in scope do not address this question. No answer was inferred from outside them."
)

REFUSAL_MESSAGE = "This request could not be answered. No answer was inferred."


def _maybe_abstaining(text: str) -> bool:
    """True while the response could still turn out to be the abstention sentinel.

    Streaming the sentinel's first characters and then retracting them would flash
    `NOT_IN_...` into the transcript before the UI replaced it.
    """
    stripped = text.strip()
    return bool(stripped) and ABSTAIN_SENTINEL.startswith(stripped[: len(ABSTAIN_SENTINEL)])


def _is_abstention(text: str) -> bool:
    return text.strip() == ABSTAIN_SENTINEL


def build_client(api_key: str, headers: dict[str, str] | None = None) -> Any:
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, default_headers=headers or None)


def build_messages(pages: list[Page], question: str) -> list[dict[str, Any]]:
    """One user message: the documents, then the request.

    Order matters twice over. The documents come first because prompt caching is
    prefix-matched, so the large stable part has to precede the part that changes per
    question. And the block order inside each document *is* the citation mapping —
    see `citations.build_documents`.
    """
    blocks, _ = build_documents(pages)
    return [
        {
            "role": "user",
            "content": [*cache_last(blocks), {"type": "text", "text": question}],
        }
    ]


async def stream_grounded(
    client: Any,
    *,
    model: str,
    system: str,
    pages: list[Page],
    question: str,
    effort: str,
    max_tokens: int,
    purpose: str,
    abstention_message: str = ABSTENTION_MESSAGE,
    cache_documents: bool = True,
    inline_markers: bool = False,
) -> AsyncIterator[GenerationEvent]:
    """Stream one cited answer. Yields deltas and citations, then exactly one terminal
    event — `Abstained` followed by `Completed`, or `Completed` alone.

    `inline_markers` writes a `{{cite:n}}` marker into the prose after each cited
    span, so the chat renderer can put a chip there. On for the workflows whose
    output *is* prose — `ask` and `draft`. Off for `summary`, whose text is parsed
    back into nine headed sections and whose fields carry their own `cites` list: a
    marker there would be parsed into the field's value and rendered as literal
    braces.
    """
    if not pages:
        yield Abstained(
            "No documents are in scope for this request, so there is nothing to answer from."
        )
        yield Completed(
            text="",
            usage=Usage(model_id=model, purpose=purpose),
            abstained=True,
        )
        return

    blocks, page_index = build_documents(pages)
    # See `cache_last`: a write costs 1.25x input and a read 0.1x, so a prefix with no
    # second reader is a surcharge rather than a saving. The system prompt is cached
    # unconditionally — it is small, and it is byte-identical on every call of this
    # workflow, so it is the one part guaranteed to be read.
    collector = CitationCollector()
    text = ""
    started = time.perf_counter()
    # Citation numbers whose marker is still owed to the current content block. The
    # API sends `citations_delta` for a block *before* that block's text, so a marker
    # written when the citation arrives lands in front of the sentence it supports
    # rather than after it. They are held here and flushed at `content_block_stop`.
    pending: list[int] = []

    async with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        # A cache breakpoint on the system prompt: it is byte-identical on every call
        # of this workflow, so it is the one part guaranteed to hit.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        # Thinking is deliberately not configured — see the module docstring. Depth is
        # controlled here and nowhere else.
        output_config={"effort": effort},
        messages=[
            {
                "role": "user",
                "content": [
                    *cache_last(blocks, enabled=cache_documents),
                    {"type": "text", "text": question},
                ],
            }
        ],
    ) as stream:
        async for event in stream:
            if event.type == "content_block_stop":
                # The block is complete, so its markers now follow its text.
                if pending and not _maybe_abstaining(text):
                    marker = "".join(citation_marker(text, n) for n in pending)
                    text += marker
                    yield TextDelta(text=marker)
                    pending = []
                continue

            if event.type != "content_block_delta":
                continue
            delta = event.delta

            if delta.type == "text_delta":
                text += delta.text
                # Held back until it is clear this is not the sentinel.
                if not _maybe_abstaining(text):
                    yield TextDelta(text=delta.text)

            elif delta.type == "citations_delta":
                citation, is_new = collector.observe(delta.citation, page_index)
                if citation is None:
                    continue
                # The citation goes out before the marker that references it, always:
                # the client resolves `{{cite:n}}` against the citation list it has,
                # so a marker that arrived first would render as a dead chip.
                if is_new:
                    yield CitationFound(citation=citation)
                if inline_markers and citation.n not in pending:
                    pending.append(citation.n)

        # A stream that ended without a closing event for its last block still owes
        # that block's markers.
        if pending and not _maybe_abstaining(text):
            marker = "".join(citation_marker(text, n) for n in pending)
            text += marker
            yield TextDelta(text=marker)
            pending = []

        final = await stream.get_final_message()

    usage = Usage(
        model_id=model,
        purpose=purpose,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    if final.usage is not None:
        usage.input_tokens = final.usage.input_tokens or 0
        usage.output_tokens = final.usage.output_tokens or 0
        usage.cache_read_tokens = getattr(final.usage, "cache_read_input_tokens", 0) or 0
        usage.cache_write_tokens = getattr(final.usage, "cache_creation_input_tokens", 0) or 0

    log.info(
        "generated",
        extra={
            "purpose": purpose,
            "model": model,
            "effort": effort,
            "pages": len(pages),
            "citations": len(collector.citations),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            # Zero across repeated calls means something earlier in the prefix is
            # varying — the only way a silent cache miss becomes visible.
            "cache_read_tokens": usage.cache_read_tokens,
            "latency_ms": usage.latency_ms,
        },
    )

    # A safety decline is not an answer. Surfaced as an abstention so the caller shows
    # "not answered from the documents" rather than a hang or a raw error.
    if final.stop_reason == "refusal":
        detail = getattr(final, "stop_details", None)
        log.warning("generation refused", extra={"category": getattr(detail, "category", None)})
        yield Abstained(REFUSAL_MESSAGE)
        yield Completed(text="", usage=usage, abstained=True, refused=True)
        return

    if _is_abstention(text):
        # Worded by the caller, because what the sentinel *means* depends on what the
        # model was shown: "the documents do not address this" is a stronger claim
        # than "the excerpts retrieved do not", and only one of them is provable from
        # a partial corpus.
        yield Abstained(abstention_message)
        yield Completed(text="", usage=usage, abstained=True)
        return

    yield Completed(text=text, citations=collector.citations, usage=usage)
