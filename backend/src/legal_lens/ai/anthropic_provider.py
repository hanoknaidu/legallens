"""Generation through Claude, with native citations.

One prompt per intent, over the pages the runner selected. The orchestration layer
(`llm_provider="orchestrator"`) replaces this with three distinct workflows that
retrieve for themselves; this provider remains the simpler path, and the one that
needs no local models.

**Document assembly and citation resolution are not implemented here.** Both come
from `orchestrator/citations.py`, which owns the decision that pages are the content
blocks of a custom-content document and that `start_block_index` is therefore a page
number by position. That logic existed twice until the two paths were joined, which
is precisely the kind of duplication that lets one copy start citing the wrong page.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from ..config import Settings
from ..orchestrator.citations import CitationCollector, build_documents, citation_marker
from ..schemas import Citation, DraftPayload, SummaryField
from .adapt import to_orch_pages, to_wire_citation
from .types import (
    Abstained,
    CitationFound,
    Finished,
    GenerationEvent,
    GenerationRequest,
    Structured,
    TextDelta,
    Usage,
)

log = logging.getLogger(__name__)

ABSTAIN_SENTINEL = "NOT_IN_DOCUMENTS"

BASE_SYSTEM = f"""You are a legal research assistant answering strictly from the \
documents provided.

Rules, in order of precedence:

1. Every factual assertion must be supported by a citation to the documents. Quote \
the supporting language; do not paraphrase a source and present it as fact.
2. If the documents do not answer the question, reply with exactly \
{ABSTAIN_SENTINEL} and nothing else. Do not substitute general legal knowledge, do \
not reason from what a contract of this type usually says, and do not speculate. An \
honest "not addressed" is the correct and expected answer when the documents are \
silent.
3. Never state a conclusion the documents do not support, even if the user's question \
presupposes it.
4. Do not mention page numbers, exhibit numbers or document names in your prose. \
Citations are attached automatically and rendered separately; writing them into the \
text duplicates them and is often wrong.

You are drafting for an attorney. Be precise and terse. Prefer the document's own \
words."""

SUMMARY_INSTRUCTION = """Summarise the documents under exactly these nine headings, \
in this order, each on its own line as `## <heading>` followed by the summary:

Parties
Term & renewal
Obligations
Payment terms
Termination triggers
Governing law & venue
Liability & indemnification
Unusual provisions
Open items

If a heading is not addressed anywhere in the documents, write exactly `NOT PRESENT` \
as its body. Reporting an absence is required — omitting the heading is not \
acceptable, because a reader cannot tell a missing clause from a missing answer."""

DRAFT_INSTRUCTION = """Produce a short legal memorandum in Markdown.

Where the documents do not supply a fact the memorandum needs, write \
`[ATTORNEY INPUT REQUIRED]` inline at that point rather than inventing it or quietly \
writing around it. Reproduce that marker exactly; it is styled on render and must \
survive unchanged."""

SUMMARY_HEADINGS: tuple[tuple[str, str], ...] = (
    ("parties", "Parties"),
    ("term", "Term & renewal"),
    ("obligations", "Obligations"),
    ("payment", "Payment terms"),
    ("termination", "Termination triggers"),
    ("governingLaw", "Governing law & venue"),
    ("liability", "Liability & indemnification"),
    ("unusual", "Unusual provisions"),
    ("openItems", "Open items"),
)


class AnthropicProvider:
    """Streams a cited answer from Claude."""

    name = "anthropic"
    #: The runner selects the pages. The orchestrator provider is the one that does
    #: not — see `ai/types.py`.
    owns_retrieval = False

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "llm_provider='anthropic' needs LEGAL_LENS_ANTHROPIC_API_KEY; "
                "set it or use llm_provider='echo'"
            )
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            default_headers=settings.anthropic_headers or None,
        )
        self._settings = settings

    def _instruction(self, intent: str) -> str:
        if intent == "summary":
            return SUMMARY_INSTRUCTION
        if intent == "draft":
            return DRAFT_INSTRUCTION
        return ""

    # ----------------------------------------------------------------- stream

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        if not request.pages:
            yield Abstained(
                "No documents are in scope for this question, so there is nothing to answer from."
            )
            yield Finished(usage=Usage(model_id=self._settings.generation_model))
            return

        # Pages become one custom-content document per source document, and the block
        # order is the citation mapping. Both live in `orchestrator/citations.py`.
        blocks, page_index = build_documents(to_orch_pages(request.pages))
        instruction = self._instruction(request.intent)
        question = (
            f"{instruction}\n\nThe attorney's request: {request.question}"
            if instruction
            else request.question
        )

        max_tokens = (
            self._settings.draft_max_output_tokens
            if request.intent == "draft"
            else self._settings.max_output_tokens
        )

        usage = Usage(
            model_id=self._settings.generation_model,
            purpose=f"generation:{request.intent}",
        )
        text = ""
        citations: list[Citation] = []
        # Resolves a block index to a page and drops spans the model cited twice.
        collector = CitationCollector()
        # `ask` and `draft` produce prose, and each cited claim in it carries a chip.
        # `summary` does not: its text is parsed back into nine headed sections and
        # the fields carry their own `cites`, so a marker would end up rendered as
        # literal braces inside a field's value.
        inline_markers = request.intent != "summary"
        # Citation numbers owed to the current content block. The API sends a block's
        # `citations_delta` *before* its text, so a marker written on arrival lands in
        # front of the sentence it supports; they are flushed at `content_block_stop`.
        pending: list[int] = []

        # Streaming because the documents are large and `max_tokens` is high; a
        # non-streaming call at this size risks the SDK's HTTP timeout.
        #
        # Thinking is left at its default (adaptive, always on for this model) and
        # depth is controlled by `effort` — which must be set explicitly, because
        # this model's default is `medium` where the config asks for `high`.
        async with self._client.messages.stream(
            model=self._settings.generation_model,
            max_tokens=max_tokens,
            system=BASE_SYSTEM,
            output_config={"effort": self._settings.generation_effort},
            messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": question}]}],
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
                    # Abstention is decided on the whole response, so tokens are not
                    # forwarded until it is clear this is not a refusal to answer.
                    if not _maybe_abstaining(text):
                        yield TextDelta(text=delta.text)

                elif delta.type == "citations_delta":
                    found, is_new = collector.observe(delta.citation, page_index)
                    if found is None:
                        continue
                    # The citation goes out before the marker that references it: the
                    # client resolves `{{cite:n}}` against the citations it has, so a
                    # marker that arrived first would render as a dead chip.
                    if is_new:
                        resolved = to_wire_citation(found)
                        citations.append(resolved)
                        yield CitationFound(citation=resolved)
                    if inline_markers and found.n not in pending:
                        pending.append(found.n)

            # A stream that ended without a closing event for its last block still
            # owes that block's markers.
            if pending and not _maybe_abstaining(text):
                marker = "".join(citation_marker(text, n) for n in pending)
                text += marker
                yield TextDelta(text=marker)
                pending = []

            final = await stream.get_final_message()

        if final.usage is not None:
            usage.input_tokens = final.usage.input_tokens or 0
            usage.output_tokens = final.usage.output_tokens or 0
            usage.cache_read_tokens = getattr(final.usage, "cache_read_input_tokens", 0) or 0
            usage.cache_write_tokens = getattr(final.usage, "cache_creation_input_tokens", 0) or 0

        # A safety decline is not an answer. Reported as an abstention so the UI shows
        # "not answered from the documents" rather than a hang or a raw error.
        if final.stop_reason == "refusal":
            detail = getattr(final, "stop_details", None)
            log.warning(
                "generation refused",
                extra={"category": getattr(detail, "category", None)},
            )
            yield Abstained("This request could not be answered. No answer was inferred.")
            yield Finished(usage=usage)
            return

        if _is_abstention(text):
            yield Abstained(
                "The documents in scope do not address this question. No answer was "
                "inferred from outside them."
            )
            yield Finished(usage=usage)
            return

        if request.intent == "summary":
            yield Structured(summary=_parse_summary(text, citations))
        elif request.intent == "draft":
            yield Structured(
                draft=DraftPayload(
                    title=request.question.strip().rstrip("?").title() or "Draft",
                    body=text.strip(),
                )
            )

        yield Finished(usage=usage)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _maybe_abstaining(text: str) -> bool:
    """True while the response could still turn out to be the abstention sentinel.

    Streaming the sentinel's first characters to the user and then retracting them
    would flash `NOT_IN_...` into the transcript.
    """
    stripped = text.strip()
    return bool(stripped) and ABSTAIN_SENTINEL.startswith(stripped[: len(ABSTAIN_SENTINEL)])


def _is_abstention(text: str) -> bool:
    return text.strip() == ABSTAIN_SENTINEL


def _parse_summary(text: str, citations: list[Citation]) -> list[SummaryField]:
    """Split the nine headings out of the response.

    A heading the model omitted entirely is returned with `value: None`, the same as
    one it marked `NOT PRESENT` — FR-13 requires the field to be present and the
    absence to be explicit either way, so the renderer has something to show.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = stripped.lstrip("#").strip().lower()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()

    all_cites = [c.n for c in citations]
    fields: list[SummaryField] = []
    for key, label in SUMMARY_HEADINGS:
        body = sections.get(label.lower(), "").strip()
        absent = not body or body.upper().startswith("NOT PRESENT")
        fields.append(
            SummaryField(
                key=key,
                label=label,
                value=None if absent else body,
                # Citations arrive for the response as a whole rather than per
                # heading, so an absent field claims none and a present one points at
                # the set. Attributing a specific citation to a specific heading would
                # be a guess.
                cites=[] if absent else all_cites,
            )
        )
    return fields
