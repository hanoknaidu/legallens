"""Pre-retrieval query rewriting with Claude Haiku, via LangChain.

A follow-up question is usually not searchable on its own. *"What about
termination?"* retrieves nothing useful; *"What does the Arbor MSA say about
termination?"* retrieves the right clause. Resolving that reference before retrieval
runs is the single cheapest improvement available to the ask workflow.

Three things about the design, in the order they matter.

**Declining has to be sayable.** A rewrite that happens to equal the original is
indistinguishable from a refusal if the only output is a string. So `action` is an
enum in the tool schema, the same construction `intent.py` uses: an off-menu answer is
impossible by the shape of the response rather than by validation after the fact.

**An empty history is handled in code, not in the prompt.** A prompt can be
disobeyed; a branch cannot. It also saves a call and its latency on every first
question in a conversation, which is a large fraction of all questions.

**A bad rewrite is worse than no rewrite**, because it fails invisibly: retrieval
fetches the wrong passages and the attorney sees a confident answer to a question they
did not ask. Everything in `rewrite_prompt.md` is arranged around making the model
decline when it is unsure.

Model constraint: **`output_config.effort` is rejected by Haiku 4.5.** Do not send it.
There is no thinking configuration worth setting for a mechanical rewrite either.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import OrchestratorConfig
from ..types import Turn, Usage
from ..usage import split_input_tokens

log = logging.getLogger(__name__)

PROMPT_FILENAME = "rewrite_prompt.md"

#: A cache breakpoint on the system prompt, which is the large stable part of every
#: one of these calls. The question that follows it is a few dozen tokens; the guide
#: above it is over a thousand, re-sent on every message in the conversation.
#:
#: **This only works because of the model.** Anthropic's minimum cacheable prefix is
#: per-model, and it is not monotonic across generations: Haiku 4.5 requires **4096
#: tokens**, while Haiku 5.5 requires **512**. Measured, these two prompts are 1742
#: and 1274 tokens — so on Haiku 4.5 a breakpoint here cached *nothing*, silently,
#: with `cache_creation_input_tokens: 0` and no error. `workflows-plan.md` §9.3
#: prescribed exactly that fix and it would have changed nothing.

#: How many prior turns the model is shown. A follow-up resolves against the recent
#: conversation; twenty turns ago is noise that invites spurious rewrites, and the
#: longer the history the more likely the model finds *something* to resolve against.
DEFAULT_HISTORY_TURNS = 6

#: Per turn, so one pasted contract in the history cannot crowd out the question.
MAX_TURN_CHARS = 1200

#: A rewrite longer than this is not a question any more — it is a summary of the
#: conversation, which retrieval handles worse than the original question did.
MAX_REWRITE_CHARS = 400


def load_prompt() -> str:
    """Read the rewriting guide that ships beside this package.

    Markdown rather than a string literal so the guardrails can be reviewed and edited
    as the subject-matter guidance they are, without touching Python.
    """
    return (Path(__file__).parents[1] / PROMPT_FILENAME).read_text(encoding="utf-8")


class _Rewrite(BaseModel):
    """The tool schema the model fills in.

    Field descriptions reach the model as part of the prompt, so they are written for
    it rather than for a reader of this file.
    """

    action: Literal["rewrite", "no_rewrite"] = Field(
        description=(
            "'rewrite' if the question needs the conversation to be understood and you "
            "can resolve it without guessing. 'no_rewrite' if the question already "
            "stands alone, is about a different subject, has no referent in the "
            "history, or could be resolved two different ways."
        )
    )
    question: str | None = Field(
        default=None,
        description=(
            "The self-contained question, when action is 'rewrite'. One sentence, in "
            "the attorney's own words, resolving only the references the history "
            "actually resolves. Leave empty when action is 'no_rewrite'."
        ),
    )
    reason: str = Field(
        description=(
            "One short sentence naming what you resolved or why you declined, for an "
            "engineer reading a log. Do not restate the question."
        )
    )


@dataclass(frozen=True)
class RewriteResult:
    """What the rewrite step concluded, and how it got there.

    Every field here is meant to reach the audit row. When an answer turns out wrong,
    the first question is always whether retrieval was looking for the right thing,
    and that is only answerable if the original, the verdict and the rewrite were all
    recorded.
    """

    #: What retrieval should actually search for. Equal to `original` when declined.
    question: str
    original: str
    rewritten: bool
    reason: str
    #: `empty_history`, `model`, `declined`, `unusable`, `error`.
    source: str
    usage: Usage | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _format_history(history: list[Turn], limit: int) -> str:
    recent = history[-limit:] if limit > 0 else []
    lines = []
    for turn in recent:
        content = turn.content.strip()
        if len(content) > MAX_TURN_CHARS:
            content = content[:MAX_TURN_CHARS] + " […]"
        lines.append(f"{'Attorney' if turn.role == 'user' else 'Assistant'}: {content}")
    return "\n\n".join(lines)


class QueryRewriter:
    """Rewrites a follow-up question, or declines to."""

    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        *,
        prompt: str | None = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        max_tokens: int = 512,
        max_retries: int = 2,
        chain: Any = None,
    ) -> None:
        self._config = config or OrchestratorConfig()
        self._prompt = prompt if prompt is not None else load_prompt()
        self._history_turns = history_turns

        if chain is not None:
            # Injected by the tests, so the guardrails can be exercised without a
            # vendor call or an API key.
            self._chain = chain
            return

        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=self._config.rewrite_model,
            api_key=self._config.require_api_key(),
            max_tokens=max_tokens,
            timeout=self._config.timeout,
            max_retries=max_retries,
            default_headers=self._config.headers,
            # No `temperature`. It used to be 0, on the reasoning that rewriting the
            # same question two ways on two days is a bug rather than variety — but
            # Haiku 5.5 rejects a non-default sampling parameter with a 400, so the
            # choice is not available. What actually bounds the answer is the enum in
            # `_Rewrite.action`, which no sampling temperature could widen.
            #
            # No `output_config` either: `effort` is rejected on this tier.
        )
        # `function_calling` rather than `json_schema`: tool-shaped structured output
        # is the broadly supported path on this tier, and the enum in the schema is
        # what actually constrains the answer.
        self._chain = llm.with_structured_output(
            _Rewrite, method="function_calling", include_raw=True
        )

    def _messages(self, history: str, question: str) -> list[Any]:
        """The two messages, with the system prompt marked cacheable.

        The system prompt is sent as a content block rather than a bare string so the
        `cache_control` marker has somewhere to live; LangChain forwards `type`,
        `text`, `cache_control` and `citations` on a text block through to the API.
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
            HumanMessage(
                content=(f"Conversation so far:\n\n{history}\n\nFollow-up question:\n\n{question}")
            ),
        ]

    @property
    def model(self) -> str:
        return self._config.rewrite_model

    def rewrite(self, question: str, history: list[Turn] | None = None) -> RewriteResult:
        """Never raises. A failed call returns the original question unchanged.

        That fallback is the safe one: searching for what the attorney actually typed
        is always defensible, while a rewrite produced by a degraded path is not.
        """
        original = (question or "").strip()
        turns = [t for t in (history or []) if t.content.strip()]

        # Guardrail one, as code. There is nothing to resolve a reference against, so
        # there is no reason to spend a call discovering that.
        if not turns:
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason="No conversation history, so nothing to resolve against.",
                source="empty_history",
            )

        if not original:
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason="The question is empty.",
                source="empty_history",
            )

        started = time.perf_counter()
        try:
            envelope = self._chain.invoke(
                self._messages(_format_history(turns, self._history_turns), original)
            )
        except Exception as exc:  # noqa: BLE001 — a rewrite failure must not stop the answer
            log.warning(
                "query rewrite failed; using the original question",
                extra={"error_type": type(exc).__name__},
            )
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason="The rewrite call failed; the original question was used.",
                source="error",
                error=f"{type(exc).__name__}: {exc}",
                usage=Usage(
                    model_id=self._config.rewrite_model,
                    purpose="rewrite",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        parsed: _Rewrite | None = envelope.get("parsed")
        raw_message = envelope.get("raw")
        counts = getattr(raw_message, "usage_metadata", None) or {}
        fresh, cache_read, cache_write = split_input_tokens(counts)
        usage = Usage(
            model_id=self._config.rewrite_model,
            input_tokens=fresh,
            output_tokens=counts.get("output_tokens", 0),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            purpose="rewrite",
            latency_ms=elapsed,
        )

        if parsed is None:
            log.warning("query rewrite returned no parsable tool call")
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason="The model did not return a verdict.",
                source="error",
                error=str(envelope.get("parsing_error") or "no parsed output"),
                usage=usage,
            )

        if parsed.action == "no_rewrite":
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason=parsed.reason,
                source="declined",
                usage=usage,
            )

        candidate = (parsed.question or "").strip()

        # The model said `rewrite` and then supplied nothing usable. Treated as a
        # decline rather than as an error: the outcome is the same question either
        # way, and the log says which path produced it.
        if not candidate:
            log.info("rewrite requested with no question; keeping the original")
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason="The model asked to rewrite but returned no question.",
                source="unusable",
                usage=usage,
            )

        # A "rewrite" that is the original is a decline the model expressed the long
        # way round. Recording it as one keeps the audit honest about what happened.
        if candidate == original:
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason=parsed.reason,
                source="declined",
                usage=usage,
            )

        if len(candidate) > MAX_REWRITE_CHARS:
            # Long enough to be a summary of the conversation rather than a question,
            # which retrieves worse than the original did.
            log.info(
                "rewrite too long; keeping the original",
                extra={"chars": len(candidate), "limit": MAX_REWRITE_CHARS},
            )
            return RewriteResult(
                question=original,
                original=original,
                rewritten=False,
                reason=f"The rewrite ran to {len(candidate)} characters and was discarded.",
                source="unusable",
                usage=usage,
            )

        log.info(
            "question rewritten",
            extra={"original_chars": len(original), "rewritten_chars": len(candidate)},
        )
        return RewriteResult(
            question=candidate,
            original=original,
            rewritten=True,
            reason=parsed.reason,
            source="model",
            usage=usage,
            raw={"model": self._config.rewrite_model},
        )
