"""The one adapter between the orchestration layer and the service.

`orchestrator/types.py` is deliberately plain dataclasses that import nothing from the
rest of `legal_lens`, which is what lets a prompt be tuned by running one CLI command
instead of booting an API and a database. The cost of that independence is exactly one
translation, and this is it. Keeping it in a single module is what stops the two type
universes leaking into each other: nothing outside here knows both names for a page.

The direction is one-way on purpose. The service imports the orchestrator; the
orchestrator imports nothing back. Adding an import in the other direction would
re-couple the thing that independence was bought for.

Only the two leaf modules of the orchestrator are touched at import time
(`citations`, `types`), both of which are plain dataclasses — so importing this costs
nothing and works whether or not the `workflows` extra (torch, Qdrant) is installed.
"""

from __future__ import annotations

from ..orchestrator.types import Citation as OrchCitation
from ..orchestrator.types import Page as OrchPage
from ..orchestrator.types import Turn as OrchTurn
from ..orchestrator.types import Usage as OrchUsage
from ..schemas import Citation, Message, SummaryField
from .types import HistoryTurn, SourcePage, Usage

# ---------------------------------------------------------------------------
# Into the orchestrator
# ---------------------------------------------------------------------------


def to_orch_page(page: SourcePage) -> OrchPage:
    """`SourcePage` and `Page` are the same four fields under different names."""
    return OrchPage(
        doc_id=page.document_id,
        title=page.document_title,
        page=page.page,
        text=page.text,
    )


def to_orch_pages(pages: list[SourcePage]) -> list[OrchPage]:
    """Order is preserved, because order *is* the citation mapping.

    `build_documents` maps a citation's `start_block_index` back to a page by
    position, so reordering here would make the same citation name a different page.
    """
    return [to_orch_page(p) for p in pages]


def to_orch_turns(history: list[HistoryTurn]) -> list[OrchTurn]:
    return [OrchTurn(role=t.role, content=t.content) for t in history]


def history_from_messages(messages: list[Message]) -> list[HistoryTurn]:
    """Prior turns as the rewrite step reads them, oldest first.

    Abstentions are dropped. "The documents do not address this" is not a subject a
    follow-up can refer back to, and leaving it in invites the rewrite step to carry
    the unanswered *question* forward as though it had been answered — which is the
    failure mode §9.2 found twice: inheriting a predicate instead of a subject.
    """
    out: list[HistoryTurn] = []
    for message in messages:
        if message.abstained:
            continue
        content = (message.content or "").strip()
        if not content:
            continue
        out.append(HistoryTurn(role=message.role, content=content))
    return out


# ---------------------------------------------------------------------------
# Out of the orchestrator
# ---------------------------------------------------------------------------


def to_wire_citation(citation: OrchCitation) -> Citation:
    return Citation(
        n=citation.n,
        document_id=citation.doc_id,
        document_title=citation.doc_title,
        page=citation.page,
        cited_text=citation.cited_text,
    )


def to_usage(usage: OrchUsage, *, purpose: str | None = None) -> Usage:
    """One orchestrator call's cost as the audit table records it.

    `purpose` is overridable because the orchestrator names a step for its own logs
    (`ask`) while the cost table names a feature (`generation:ask`), and the two
    vocabularies are not worth unifying across a seam.
    """
    return Usage(
        model_id=usage.model_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        purpose=purpose if purpose is not None else usage.purpose,
        latency_ms=usage.latency_ms,
    )


def to_wire_summary_field(field: object) -> SummaryField:
    """The orchestrator's `SummaryField` onto the wire's.

    Typed loosely because the orchestrator's version lives in `workflows/summary.py`,
    which is only importable with the `workflows` extra installed; this module stays
    importable without it.
    """
    return SummaryField(
        key=field.key,  # type: ignore[attr-defined]
        label=field.label,  # type: ignore[attr-defined]
        value=field.value,  # type: ignore[attr-defined]
        cites=list(field.cites),  # type: ignore[attr-defined]
    )
