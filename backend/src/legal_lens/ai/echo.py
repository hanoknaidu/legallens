"""A deterministic generation provider that makes no network call.

This is not a mock in the testing sense — it is a supported configuration
(`llm_provider="echo"`), and it exists so the whole pipeline can be developed,
demonstrated and tested without an API key or a cent of spend.

The constraint it keeps is the one that matters: **every citation it emits quotes text
that genuinely appears on the page it names.** It finds real sentences in the supplied
pages and quotes them verbatim, so citation highlighting is exercised for real. An
echo that produced plausible-looking fake citations would let the central failure mode
through untested.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from ..lib_text import best_sentences, search_terms
from ..schemas import Citation, DraftPayload, SummaryField
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

MODEL_ID = "echo-stub"

# The nine summary fields, in the order the BRD fixes and the frontend renders.
SUMMARY_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("parties", "Parties", ("party", "parties", "between", "agreement", "entered")),
    ("term", "Term & renewal", ("term", "renewal", "effective", "expire", "commence")),
    ("obligations", "Obligations", ("shall", "obligation", "services", "deliver")),
    ("payment", "Payment terms", ("payment", "fee", "invoice", "usd", "net", "due")),
    ("termination", "Termination triggers", ("terminate", "termination", "breach", "notice")),
    ("governingLaw", "Governing law & venue", ("governing", "law", "venue", "jurisdiction")),
    ("liability", "Liability & indemnification", ("liability", "indemnif", "damages", "cap")),
    ("unusual", "Unusual provisions", ("exclusive", "notwithstanding", "waive", "assign")),
    ("openItems", "Open items", ("tbd", "to be determined", "pending", "[", "reserved")),
)

TOKEN_SPLIT = re.compile(r"\S+\s*")


class EchoProvider:
    """Answers from the supplied pages by quoting them."""

    name = "echo"
    #: The runner selects the pages; this provider quotes what it is given.
    owns_retrieval = False

    def __init__(self, *, delay_s: float = 0.0) -> None:
        # Zero in tests so the suite stays fast; a small delay in dev makes the
        # streaming UI behave the way it will against a real model.
        self._delay_s = delay_s

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        if not request.pages:
            yield Abstained(
                "No documents are in scope for this question, so there is nothing to answer from."
            )
            yield Finished(usage=Usage(model_id=MODEL_ID))
            return

        # Instruction words are stripped: "Summarise the key terms" must not search
        # the documents for "summarise".
        terms = search_terms(request.question)

        # A summary is about the documents, not about the question's wording, so it
        # is never gated on question relevance — each of its nine fields searches for
        # itself and reports its own absence (FR-13).
        if request.intent == "summary":
            async for event in self._summary(request, terms):
                yield event
            return

        matches = best_sentences(
            [(p.document_id, p.document_title, p.page, p.text) for p in request.pages],
            terms,
            limit=3,
        )

        if not matches:
            # FR-9. Nothing in the documents bears on the question, so say so rather
            # than assembling something that reads like an answer.
            yield Abstained(
                "The documents in scope do not address this question. No answer was "
                "inferred from outside them."
            )
            yield Finished(usage=Usage(model_id=MODEL_ID))
            return

        if request.intent == "draft":
            async for event in self._draft(request, matches):
                yield event
            return

        async for event in self._answer(request, matches):
            yield event

    # ------------------------------------------------------------------ ask

    async def _answer(self, request, matches) -> AsyncIterator[GenerationEvent]:
        prose = (
            f"Based on the {len(request.pages)} page(s) in scope, the documents address "
            f"this directly."
        )
        async for event in self._emit(prose):
            yield event

        for n, (document_id, title, page, sentence) in enumerate(matches, start=1):
            citation = Citation(
                n=n,
                document_id=document_id,
                document_title=title,
                page=page,
                cited_text=sentence,
            )
            # The citation is emitted before the marker that references it, so the
            # chip never renders against a citation the client has not seen.
            yield CitationFound(citation=citation)
            async for event in self._emit(f" {_quote(sentence)} {{{{cite:{n}}}}}"):
                yield event

        yield Finished(usage=_usage(request, matches))

    # -------------------------------------------------------------- summary

    async def _summary(self, request, terms) -> AsyncIterator[GenerationEvent]:
        fields: list[SummaryField] = []
        citations: list[Citation] = []

        for key, label, field_terms in SUMMARY_FIELDS:
            found = best_sentences(
                [(p.document_id, p.document_title, p.page, p.text) for p in request.pages],
                list(field_terms),
                limit=1,
            )
            if not found:
                # FR-13: an absent clause is reported as absent, with the field
                # present and the value null — never omitted from the response.
                fields.append(SummaryField(key=key, label=label, value=None, cites=[]))
                continue

            document_id, title, page, sentence = found[0]
            n = len(citations) + 1
            citation = Citation(
                n=n,
                document_id=document_id,
                document_title=title,
                page=page,
                cited_text=sentence,
            )
            citations.append(citation)
            yield CitationFound(citation=citation)
            fields.append(SummaryField(key=key, label=label, value=sentence, cites=[n]))

        # Structured payloads resolve as a unit: a half-rendered summary table reads
        # as broken, so no tokens are streamed for this intent.
        await asyncio.sleep(self._delay_s * 4)
        yield Structured(summary=fields)
        yield Finished(usage=_usage(request, [(None, None, None, f.value or "") for f in fields]))

    # ---------------------------------------------------------------- draft

    async def _draft(self, request, matches) -> AsyncIterator[GenerationEvent]:
        citations: list[Citation] = []
        lines = [f"## {request.question.strip().rstrip('?').title()}", ""]

        for n, (document_id, title, page, sentence) in enumerate(matches, start=1):
            citations.append(
                Citation(
                    n=n,
                    document_id=document_id,
                    document_title=title,
                    page=page,
                    cited_text=sentence,
                )
            )
            lines += [f"{_quote(sentence)} {{{{cite:{n}}}}}", ""]

        lines += [
            "### Analysis",
            "",
            "[ATTORNEY INPUT REQUIRED] — the conclusion below must be reviewed before "
            "this memorandum is relied upon or sent.",
            "",
            "The provisions quoted above are the only ones in scope that bear on the "
            "question. Facts not present in the documents are marked rather than assumed.",
        ]

        for citation in citations:
            yield CitationFound(citation=citation)
        await asyncio.sleep(self._delay_s * 4)
        yield Structured(
            draft=DraftPayload(
                title=request.question.strip().rstrip("?").title() or "Draft",
                body="\n".join(lines),
            )
        )
        yield Finished(usage=_usage(request, matches))

    # ----------------------------------------------------------------- util

    async def _emit(self, text: str) -> AsyncIterator[GenerationEvent]:
        """Chunk like a real stream — by word, with a pause — so the UI is exercised."""
        for chunk in TOKEN_SPLIT.findall(text) or [text]:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield TextDelta(text=chunk)


def _quote(sentence: str) -> str:
    return f'"{sentence}"'


def _usage(request, matches) -> Usage:
    """Plausible token counts so the metrics and cost paths are exercised.

    `echo-stub` has no price, so `cost_usd` resolves to zero — correct, because this
    provider genuinely costs nothing.
    """
    source_chars = sum(len(p.text) for p in request.pages)
    produced_chars = sum(len(str(m[3])) for m in matches)
    return Usage(
        model_id=MODEL_ID,
        input_tokens=source_chars // 4,
        output_tokens=max(1, produced_chars // 4),
    )
