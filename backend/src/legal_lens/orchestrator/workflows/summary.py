"""The summary workflow: nine fixed fields over the documents in scope.

Three decisions, each of which resolves something the BRD flags as hard.

**Retrieval is skipped — the whole document is read.** This is not the "always
retrieve" question in another guise; it falls out of what a summary is. A summary of
retrieved fragments is not a summary, and the nine fields include *Open items*, which
is an absence question by construction: you cannot report what a contract leaves
unresolved from the eight passages that best matched a query.

**Structured output and citations cannot coexist in one call** — sending
`output_config.format` alongside `citations` is a 400, which BRD §4.1 lists as a
genuine conflict and proposes two-pass generation for. One pass of `## Heading` prose
with citations enabled, parsed afterwards, sidesteps it: the headings are a format the
model can hold without a schema, and parsing them is a dozen lines.

**The model is routed by how much reconciliation is needed, not by size.**
Summarising one contract is extraction, which the smaller model does well. The hard
case is *"does Amendment No. 2 change the term in the MSA?"* — two documents that
contradict each other, where reporting the superseded figure is a confident wrong
answer. That is what the larger model is for. See `OrchestratorConfig.summary_model_for`.

A heading the model skipped and one it marked `NOT PRESENT` both resolve to
`value=None`, because FR-13 requires the field to exist and the absence to be explicit
either way — the renderer has to have something to show.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import OrchestratorConfig
from ..retrieve import Retriever
from ..types import Citation, Page, Usage
from .generate import (
    Abstained,
    CitationFound,
    Completed,
    GenerationEvent,
    TextDelta,
    build_client,
    stream_grounded,
)

log = logging.getLogger(__name__)

PROMPT_FILENAME = "summary_prompt.md"

#: `(key, heading)`. The keys match `schemas.SummaryField` so the integration adapter
#: is a rename and nothing more.
#: Whether the first pass writes its documents to the cache.
#:
#: **Off, and it is arithmetic rather than taste.** A cache write costs 1.25x the
#: input rate and a read 0.1x, so writing the ~20K-token corpus costs an extra 0.25x
#: every time, and the only request that could ever read it is the retry pass. For
#: the write to pay for itself:
#:
#:     write surcharge          0.25 x corpus, on every summary
#:     saving when retried      0.90 x corpus, on the ones that retry
#:     break-even               retry rate > ~25%
#:
#: The observed retry rate is far below that — both end-to-end runs populated 9/9
#: headings on the first pass, which is the thing the retry exists to catch. So the
#: write was a surcharge on every summary in exchange for a saving on almost none.
#:
#: Flip to `True` if the retry rate is ever measured above ~25%. The figure to look
#: at is the ratio of `summary:retry` to `summary` rows in `usage_costs`, which
#: `GET /audit/costs` now reports.
CACHE_DOCUMENTS = False

HEADINGS: tuple[tuple[str, str], ...] = (
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

ABSENT = "NOT PRESENT"

#: Titles that mark a document as amending another. An amendment in scope routes to
#: the larger model however few documents there are, because reconciling it against
#: the agreement it changes is the expensive reasoning this routing exists for.
_AMENDMENT = re.compile(r"\b(amendment|amended|addendum|rider|supplement)\b", re.I)


def load_prompt() -> str:
    return (Path(__file__).parents[1] / PROMPT_FILENAME).read_text(encoding="utf-8")


def looks_like_amendment(title: str) -> bool:
    return bool(_AMENDMENT.search(title))


@dataclass(frozen=True)
class SummaryField:
    key: str
    label: str
    #: `None` for an absent field — whether the model said `NOT PRESENT` or skipped
    #: the heading entirely.
    value: str | None
    cites: list[int] = field(default_factory=list)


@dataclass
class SummaryResult:
    fields: list[SummaryField] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    model: str = ""
    documents: int = 0
    pages: int = 0
    #: True when a second pass was spent filling headings the first one skipped.
    retried: bool = False
    abstained: bool = False
    usage: list[Usage] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        """Headings with no content. Reported, not hidden: a summary that quietly
        dropped three of its nine fields looks complete and is not."""
        return [f.label for f in self.fields if f.value is None]


def parse_sections(text: str) -> dict[str, str]:
    """Split `## Heading` sections out of the response.

    Matched case-insensitively on the heading text, because a model that writes
    `## Governing Law & Venue` has done what was asked and should not lose the field
    to a capital letter.
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
    return sections


def build_fields(text: str, citations: list[Citation]) -> list[SummaryField]:
    sections = parse_sections(text)
    all_cites = [c.n for c in citations]
    fields: list[SummaryField] = []
    for key, label in HEADINGS:
        body = sections.get(label.lower(), "").strip()
        absent = not body or body.upper().startswith(ABSENT)
        fields.append(
            SummaryField(
                key=key,
                label=label,
                value=None if absent else body,
                # Citations arrive for the response as a whole rather than per
                # heading. An absent field claims none; a present one points at the
                # set. Attributing a specific citation to a specific heading would be
                # a guess, and a guess rendered as provenance is worse than none.
                cites=[] if absent else all_cites,
            )
        )
    return fields


class SummaryWorkflow:
    def __init__(
        self,
        config: OrchestratorConfig,
        retriever: Retriever,
        *,
        client: Any = None,
        prompt: str | None = None,
    ) -> None:
        self._config = config
        self._retriever = retriever
        self._prompt = prompt if prompt is not None else load_prompt()
        self._client = client or build_client(config.require_api_key(), config.headers)

    def model_for(self, pages: list[Page]) -> str:
        titles = {p.title for p in pages}
        return self._config.summary_model_for(
            documents=len({p.doc_id for p in pages}),
            has_amendment=any(looks_like_amendment(t) for t in titles),
        )

    async def run(
        self,
        *,
        doc_ids: set[str] | None = None,
        request: str = "Summarise the documents in scope.",
    ) -> AsyncIterator[GenerationEvent | SummaryResult]:
        retrieved = await self._retriever.whole(doc_ids)
        model = self.model_for(retrieved.pages)

        result = SummaryResult(
            model=model,
            documents=retrieved.documents,
            pages=len(retrieved.pages),
        )
        log.info(
            "summary",
            extra={
                "model": model,
                "documents": result.documents,
                "pages": result.pages,
            },
        )

        text = ""
        async for event in stream_grounded(
            self._client,
            model=model,
            system=self._prompt,
            pages=retrieved.pages,
            question=request,
            effort=self._config.summary_effort,
            max_tokens=self._config.max_output_tokens,
            purpose="summary",
            cache_documents=CACHE_DOCUMENTS,
        ):
            if isinstance(event, Completed):
                text = event.text
                result.citations = event.citations
                result.abstained = event.abstained
                result.usage.append(event.usage)
            elif isinstance(event, Abstained):
                result.abstained = True
                yield event
            elif isinstance(event, TextDelta | CitationFound):
                yield event

        if result.abstained:
            yield result
            return

        result.fields = build_fields(text, result.citations)

        # One retry, not a loop. A model that skipped headings once will usually fill
        # them when asked directly; one that skips them twice is telling you the
        # documents do not support those fields, and asking a third time just spends
        # money to hear it again.
        if result.missing:
            log.info("summary incomplete; one retry", extra={"missing": result.missing})
            result.retried = True
            async for event in self._fill(retrieved.pages, model, result, text):
                if isinstance(event, TextDelta | CitationFound):
                    yield event

        yield result

    async def _fill(
        self,
        pages: list[Page],
        model: str,
        result: SummaryResult,
        first_pass: str,
    ) -> AsyncIterator[GenerationEvent]:
        """Second pass over the headings the first one left empty."""
        missing = result.missing
        request = (
            "Your previous summary did not cover these headings:\n\n"
            + "\n".join(f"- {label}" for label in missing)
            + "\n\nWrite only those headings now, in the same `## <heading>` format "
            "and under the same rules. If a heading genuinely has nothing to report "
            f"in the documents, give it exactly `{ABSENT}` as its body — but check "
            "first, because every one of these was left out rather than marked "
            "absent."
        )

        text = ""
        # Citations keep numbering from where the first pass stopped, so the two
        # passes produce one coherent chip list rather than two overlapping ones.
        offset = len(result.citations)
        async for event in stream_grounded(
            self._client,
            model=model,
            system=self._prompt,
            pages=pages,
            question=request,
            effort=self._config.summary_effort,
            max_tokens=self._config.max_output_tokens,
            purpose="summary:retry",
            # Nothing follows the retry, so a write here could have no reader at all.
            cache_documents=False,
        ):
            if isinstance(event, Completed):
                text = event.text
                result.usage.append(event.usage)
                for citation in event.citations:
                    renumbered = Citation(
                        n=citation.n + offset,
                        doc_id=citation.doc_id,
                        doc_title=citation.doc_title,
                        page=citation.page,
                        cited_text=citation.cited_text,
                    )
                    result.citations.append(renumbered)
                    yield CitationFound(citation=renumbered)
            elif isinstance(event, TextDelta):
                yield event

        if not text:
            return

        # Merge: the second pass only ever fills gaps. A heading the first pass
        # answered is never overwritten, so a retry cannot make the summary worse.
        filled = parse_sections(text)
        merged = parse_sections(first_pass)
        for heading, body in filled.items():
            if not merged.get(heading):
                merged[heading] = body
        rebuilt = "\n\n".join(f"## {heading}\n{body}" for heading, body in merged.items())
        result.fields = build_fields(rebuilt, result.citations)

        if result.missing:
            log.info(
                "summary still incomplete after retry",
                extra={"missing": result.missing},
            )
