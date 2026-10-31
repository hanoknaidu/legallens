"""Intent classification with Claude Haiku, via LangChain.

Haiku is the right tier here and the reason is structural, not budgetary: the task is
a three-way choice over one short message with no document context, and the output is
constrained to an enum by the tool schema. There is nothing for a larger model to be
better at.

Two decisions worth knowing about before reading the code:

**`ask` is the fallback, because routing errors are not symmetric.** Of the three
workflows, only `ask` can conclude "the documents do not address this" and stop.
`summary` asserts nine fixed fields; `draft` produces a document the attorney may put
their name on. So an unclassifiable message, a low-confidence classification, or a
failed model call all resolve to `ask` — the outcome that fails safely.

**The model never sees a free-text intent field.** The schema constrains `intent` to
an enum, so an off-menu answer is impossible by construction rather than by
validation after the fact.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .usage import split_input_tokens

log = logging.getLogger(__name__)

#: Duplicated from `schemas.Intent` on purpose: this package imports nothing from the
#: rest of `legal_lens`, which is what keeps it independently runnable. The cost is
#: that the two can drift — if one gains a fourth intent and the other does not, the
#: classifier returns something the API cannot persist, and nothing raises. Keep them
#: identical.
Intent = Literal["ask", "summary", "draft"]

#: Haiku 4.5. Chosen explicitly rather than inherited from a shared setting, because
#: this call must stay cheap even if the generation models are changed.
DEFAULT_MODEL = "claude-haiku-4-5"

#: The safe default. See the module docstring.
FALLBACK_INTENT: Intent = "ask"

#: Below this, the classification is not trusted and `ask` is used instead. The
#: classifier still reports what the model said, so the caller can see what happened.
DEFAULT_MIN_CONFIDENCE = 0.35

#: A classification needs the request, not an essay. Anything longer is almost
#: certainly a pasted document, and the first paragraphs carry the request.
MAX_INPUT_CHARS = 4000

PROMPT_FILENAME = "intent_prompt.md"


def load_prompt() -> str:
    """Read the classification guide that ships beside this module.

    Kept as Markdown rather than a string literal so it can be reviewed and edited as
    subject-matter guidance — which is what it is — without touching Python.
    """
    return (Path(__file__).with_name(PROMPT_FILENAME)).read_text(encoding="utf-8")


class _Classification(BaseModel):
    """The tool schema the model fills in.

    Field descriptions are part of the prompt as far as the model is concerned, so
    they are written for it rather than for a reader of this file.
    """

    intent: Intent = Field(
        description=(
            "Which workflow should handle this message. 'ask' answers a question about "
            "what the documents say. 'summary' produces the fixed nine-field overview "
            "of a document. 'draft' produces new prose the attorney will edit and send."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are that this is the workflow the attorney wants, from "
            "0 to 1. Be honest rather than generous: a low score on an ambiguous "
            "message is more useful than a high score on a guess."
        ),
    )
    reasoning: str = Field(
        description=("One sentence naming the signal you used. Do not restate the message.")
    )


@dataclass(frozen=True)
class IntentResult:
    """What the classifier concluded, and how it got there."""

    intent: Intent
    confidence: float
    reasoning: str
    #: What the model actually said, before the confidence floor was applied. Equal to
    #: `intent` unless the floor overrode it.
    model_intent: Intent | None = None
    #: How the result was reached: `model`, `low_confidence`, `empty_input`, or `error`.
    source: str = "model"
    latency_ms: int = 0
    #: Fresh input tokens only — cache reads and writes are the two fields below, in
    #: Anthropic's convention rather than LangChain's. See `workflows/usage.py`:
    #: recording LangChain's inclusive total alongside these would bill every cached
    #: token twice.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: Set when the model call failed and the fallback was used.
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def overridden(self) -> bool:
        return self.model_intent is not None and self.model_intent != self.intent


class IntentClassifier:
    """Routes a message to `ask`, `summary` or `draft`."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_tokens: int = 512,
        timeout: float = 20.0,
        max_retries: int = 2,
        prompt: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        # An API key that is not scoped to a workspace authenticates and then
        # fails every request with a 400 naming this header.
        workspace = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Put it in backend/.env "
                "(see .env.example) or export it."
            )

        self._model_name = model
        self._min_confidence = min_confidence
        self._prompt = prompt if prompt is not None else load_prompt()

        from langchain_anthropic import ChatAnthropic

        # No `temperature`. It used to be 0, on the reasoning that routing the same
        # message two ways on two days is a bug rather than variety — but Haiku 5.5
        # rejects a non-default sampling parameter with a 400, so the choice is not
        # available. What bounds the answer is the enum in `_Classification.intent`,
        # which no sampling temperature could widen.
        #
        # No `output_config`: `effort` is rejected on this tier, and the model takes
        # no thinking configuration worth setting for a mechanical choice.
        self._llm = ChatAnthropic(
            model=model,
            api_key=key,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=({"anthropic-workspace-id": workspace} if workspace else None),
        )
        # `function_calling` rather than `json_schema`: tool-shaped structured output
        # is the broadly supported path on this tier, and the enum in the schema is
        # what actually constrains the answer.
        self._chain = self._llm.with_structured_output(
            _Classification, method="function_calling", include_raw=True
        )

    @property
    def model(self) -> str:
        return self._model_name

    def _messages(self, text: str) -> list[Any]:
        """The two messages, with the system prompt marked cacheable.

        This prompt is ~1740 tokens and the message under it is a sentence, so the
        guide is almost the whole request — and it is identical on every message the
        service routes. Sent as a content block rather than a bare string because
        that is where `cache_control` can live; LangChain forwards `type`, `text`,
        `cache_control` and `citations` on a text block through to the API.

        **Only cacheable because of the model.** Haiku 4.5's minimum cacheable prefix
        is 4096 tokens, so this marker cached nothing there — silently, reporting
        `cache_creation_input_tokens: 0`. Haiku 5.5's minimum is 512.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        return [
            SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": self._prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            ),
            HumanMessage(content=f"Classify this message:\n\n{text}"),
        ]

    def classify(
        self, message: str, *, history: list[dict[str, str]] | None = None
    ) -> IntentResult:
        """Classify one message. Never raises — a failure returns the fallback.

        `history` is accepted and currently unused: the workflow that rewrites a
        follow-up question against the conversation is a separate step, and feeding
        history into the classifier would make it answer a different question than
        the one it is prompted for. The parameter exists so that adding it later is
        not a signature change at every call site.
        """
        text = (message or "").strip()
        if not text:
            # Nothing to classify, and no reason to spend a call finding that out.
            return IntentResult(
                intent=FALLBACK_INTENT,
                confidence=0.0,
                reasoning="The message is empty.",
                source="empty_input",
            )

        if len(text) > MAX_INPUT_CHARS:
            log.info(
                "truncating long message for classification",
                extra={"chars": len(text), "limit": MAX_INPUT_CHARS},
            )
            text = text[:MAX_INPUT_CHARS]

        started = time.perf_counter()
        try:
            envelope = self._chain.invoke(self._messages(text))
        except Exception as exc:  # noqa: BLE001 — a routing failure must not propagate
            elapsed = int((time.perf_counter() - started) * 1000)
            log.warning(
                "intent classification failed; falling back",
                extra={"error_type": type(exc).__name__},
            )
            return IntentResult(
                intent=FALLBACK_INTENT,
                confidence=0.0,
                reasoning="Classification failed; routed to the safe default.",
                source="error",
                latency_ms=elapsed,
                error=f"{type(exc).__name__}: {exc}",
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        parsed: _Classification | None = envelope.get("parsed")
        raw_message = envelope.get("raw")
        counts = getattr(raw_message, "usage_metadata", None) or {}
        fresh, cache_read, cache_write = split_input_tokens(counts)

        if parsed is None:
            # The model answered but not in the shape the schema requires.
            log.warning("intent classification returned no parsable tool call")
            return IntentResult(
                intent=FALLBACK_INTENT,
                confidence=0.0,
                reasoning="The model did not return a classification.",
                source="error",
                latency_ms=elapsed,
                error=str(envelope.get("parsing_error") or "no parsed output"),
                input_tokens=fresh,
                output_tokens=counts.get("output_tokens", 0),
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )

        trusted = parsed.confidence >= self._min_confidence
        return IntentResult(
            intent=parsed.intent if trusted else FALLBACK_INTENT,
            confidence=parsed.confidence,
            reasoning=parsed.reasoning,
            model_intent=parsed.intent,
            source="model" if trusted else "low_confidence",
            latency_ms=elapsed,
            input_tokens=fresh,
            output_tokens=counts.get("output_tokens", 0),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            raw={"model": self._model_name, "min_confidence": self._min_confidence},
        )
