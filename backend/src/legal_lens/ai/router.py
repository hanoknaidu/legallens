"""Intent routing: ask, summary or draft.

Mechanical and high-volume, so it runs on the cheap tier when a model is used at all.
The regex router is not a placeholder — it mirrors the frontend mock's rules exactly
(mocks/handlers/chat.ts), it is deterministic, free and instant, and it is what runs
under `llm_provider="echo"`.
"""

from __future__ import annotations

import logging
import re

from ..schemas import Intent

log = logging.getLogger(__name__)

SUMMARY = re.compile(r"\b(summar(y|ise|ize|ised|ized)|key terms|overview of)\b", re.I)
DRAFT = re.compile(r"\b(draft|memo|memorandum|write me|prepare a)\b", re.I)


def detect_intent(text: str) -> Intent:
    """Mirrors the mock so the UI behaves identically against either backend."""
    if SUMMARY.search(text):
        return "summary"
    if DRAFT.search(text):
        return "draft"
    return "ask"
