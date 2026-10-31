"""The FR-17 notice, in one place, for every output format.

Worded to survive being read out of context — someone opening the attachment a week
later, with no memory of the tool that produced it.
"""

from __future__ import annotations

TITLE = "UNREVIEWED DRAFT — REQUIRES ATTORNEY REVIEW"

BODY = (
    "This document was generated from the source documents named in its citations and "
    "has not been reviewed by an attorney. It is not legal advice and must not be "
    "filed, served, sent to a counterparty or relied upon until a licensed attorney "
    "has reviewed it."
)

GAPS_TEMPLATE = (
    "{count} passage(s) are marked [ATTORNEY INPUT REQUIRED]. Each marks a fact the "
    "source documents did not supply. Resolve every one before using this document."
)

NO_GAPS = (
    "No passages are marked [ATTORNEY INPUT REQUIRED], which does not mean the "
    "document is complete — only that nothing was flagged automatically."
)

SOURCES_TITLE = "Sources"

SOURCES_PREAMBLE = (
    "Every statement above is drawn from the passages below. Each is quoted verbatim "
    "from the page named, so it can be checked against the original."
)


def gap_sentence(count: int) -> str:
    return GAPS_TEMPLATE.format(count=count) if count else NO_GAPS
