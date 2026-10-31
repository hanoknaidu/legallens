"""The three workflows: ask, summary, draft.

Each owns its model, its effort level, its prompt and its retrieval shape, because
those differ for reasons that are not interchangeable — an answer is terse and
retrieved, a summary is structured and reads everything, a draft is long and marks
its own gaps.

What they share lives in `generate.py`: assembling custom-content document blocks,
resolving citations back to pages, and holding the abstention sentinel back until the
response is known not to be one.
"""

from .ask import AskResult, AskWorkflow
from .draft import MARKER, DraftResult, DraftWorkflow
from .generate import (
    ABSTAIN_SENTINEL,
    Abstained,
    CitationFound,
    Completed,
    GenerationEvent,
    TextDelta,
)
from .rewrite import QueryRewriter, RewriteResult
from .summary import SummaryField, SummaryResult, SummaryWorkflow

__all__ = [
    "ABSTAIN_SENTINEL",
    "MARKER",
    "Abstained",
    "AskResult",
    "AskWorkflow",
    "CitationFound",
    "Completed",
    "DraftResult",
    "DraftWorkflow",
    "GenerationEvent",
    "QueryRewriter",
    "RewriteResult",
    "SummaryField",
    "SummaryResult",
    "SummaryWorkflow",
    "TextDelta",
]
