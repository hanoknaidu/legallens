"""The pre-retrieval query rewrite, and its guardrails.

No vendor call anywhere in this file: the LangChain chain is injected, so every
guardrail is exercised deterministically and offline. The suite that actually asks a
model whether it *obeys* the prompt is `cli.py --rewrite-suite`, which costs money and
is not a unit test.
"""

from __future__ import annotations

import pytest

from legal_lens.orchestrator.config import OrchestratorConfig
from legal_lens.orchestrator.types import Turn
from legal_lens.orchestrator.workflows.rewrite import (
    MAX_REWRITE_CHARS,
    QueryRewriter,
    _Rewrite,
)

HISTORY = [
    Turn(role="user", content="What is the indemnification cap in the Arbor MSA?"),
    Turn(role="assistant", content="Section 8.2 caps liability at twelve months' fees."),
]


class FakeChain:
    """Returns a canned verdict and records what it was asked."""

    def __init__(self, parsed: _Rewrite | None, *, raises: Exception | None = None) -> None:
        self._parsed = parsed
        self._raises = raises
        self.calls: list[list[object]] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self._raises is not None:
            raise self._raises
        return {"parsed": self._parsed, "raw": None, "parsing_error": None}


def _rewriter(parsed: _Rewrite | None, *, raises: Exception | None = None):
    chain = FakeChain(parsed, raises=raises)
    config = OrchestratorConfig(api_key="test-key")
    return QueryRewriter(config, chain=chain, prompt="PROMPT"), chain


# ---------------------------------------------------------------------------
# Guardrail one: an empty history is code, not prompt
# ---------------------------------------------------------------------------


def test_an_empty_history_skips_the_model_entirely() -> None:
    rewriter, chain = _rewriter(_Rewrite(action="rewrite", question="anything", reason="r"))

    result = rewriter.rewrite("What about termination?", [])

    # A prompt can be disobeyed; a branch cannot. This also saves a call on every
    # first question in a conversation, which is a large fraction of all questions.
    assert chain.calls == []
    assert result.source == "empty_history"
    assert result.rewritten is False
    assert result.question == "What about termination?"


def test_whitespace_only_turns_count_as_no_history() -> None:
    rewriter, chain = _rewriter(_Rewrite(action="rewrite", question="x", reason="r"))

    result = rewriter.rewrite("What about termination?", [Turn(role="user", content="   ")])

    assert chain.calls == []
    assert result.source == "empty_history"


def test_an_empty_question_is_not_sent_either() -> None:
    rewriter, chain = _rewriter(_Rewrite(action="rewrite", question="x", reason="r"))

    result = rewriter.rewrite("   ", HISTORY)

    assert chain.calls == []
    assert result.rewritten is False


# ---------------------------------------------------------------------------
# Guardrail two: declining has to be sayable, and is respected
# ---------------------------------------------------------------------------


def test_a_decline_keeps_the_original_question() -> None:
    rewriter, _ = _rewriter(
        _Rewrite(action="no_rewrite", question=None, reason="Different subject.")
    )

    result = rewriter.rewrite("What does the NDA say about return of materials?", HISTORY)

    assert result.rewritten is False
    assert result.source == "declined"
    assert result.question == "What does the NDA say about return of materials?"
    # The reason reaches the audit row: when an answer is wrong, the first question is
    # whether retrieval was looking for the right thing.
    assert result.reason == "Different subject."


def test_a_rewrite_identical_to_the_original_is_recorded_as_a_decline() -> None:
    question = "What are the carve-outs?"
    rewriter, _ = _rewriter(_Rewrite(action="rewrite", question=question, reason="same"))

    result = rewriter.rewrite(question, HISTORY)

    # Otherwise the audit says a rewrite happened and the log shows two identical
    # strings, which is indistinguishable from a bug in the diffing.
    assert result.rewritten is False
    assert result.source == "declined"


def test_a_rewrite_with_no_question_falls_back_rather_than_failing() -> None:
    rewriter, _ = _rewriter(_Rewrite(action="rewrite", question="   ", reason="oops"))

    result = rewriter.rewrite("What about termination?", HISTORY)

    assert result.rewritten is False
    assert result.source == "unusable"
    assert result.question == "What about termination?"


# ---------------------------------------------------------------------------
# A good rewrite
# ---------------------------------------------------------------------------


def test_a_resolved_reference_is_used_for_retrieval() -> None:
    rewriter, chain = _rewriter(
        _Rewrite(
            action="rewrite",
            question="What are the carve-outs to the indemnification cap in the Arbor MSA?",
            reason="'it' refers to the indemnification cap.",
        )
    )

    result = rewriter.rewrite("Does it have any carve-outs?", HISTORY)

    assert result.rewritten is True
    assert result.source == "model"
    assert "indemnification cap" in result.question
    # The original survives alongside it, because the audit has to show both.
    assert result.original == "Does it have any carve-outs?"

    system, human = chain.calls[0]
    # The guide travels as a content block, not a bare string, because that is where
    # `cache_control` can live — it is the same text on every call and over a
    # thousand tokens of it.
    assert system.content == [
        {"type": "text", "text": "PROMPT", "cache_control": {"type": "ephemeral"}}
    ]
    assert "Does it have any carve-outs?" in human.content
    assert "indemnification cap in the Arbor MSA" in human.content, "history must be shown"


def test_only_recent_turns_are_shown() -> None:
    history = [Turn(role="user", content=f"turn {i}") for i in range(20)]
    rewriter, chain = _rewriter(_Rewrite(action="no_rewrite", question=None, reason="r"))

    rewriter._history_turns = 3
    rewriter.rewrite("and that one?", history)

    human = chain.calls[0][-1].content
    # Twenty turns ago is noise, and the longer the history the more likely the model
    # finds something spurious to resolve against.
    assert "turn 19" in human
    assert "turn 16" not in human


def test_an_overlong_rewrite_is_discarded() -> None:
    rewriter, _ = _rewriter(
        _Rewrite(action="rewrite", question="x" * (MAX_REWRITE_CHARS + 1), reason="r")
    )

    result = rewriter.rewrite("What about termination?", HISTORY)

    # At that length it is a summary of the conversation, which retrieves worse than
    # the original question did.
    assert result.rewritten is False
    assert result.source == "unusable"


# ---------------------------------------------------------------------------
# Failure never stops the answer
# ---------------------------------------------------------------------------


def test_a_failed_call_falls_back_to_the_original() -> None:
    rewriter, _ = _rewriter(None, raises=RuntimeError("upstream down"))

    result = rewriter.rewrite("Does it have carve-outs?", HISTORY)

    # Searching for what the attorney actually typed is always defensible; a rewrite
    # from a degraded path is not.
    assert result.question == "Does it have carve-outs?"
    assert result.rewritten is False
    assert result.source == "error"
    assert "upstream down" in (result.error or "")


def test_an_unparsable_response_falls_back_to_the_original() -> None:
    rewriter, _ = _rewriter(None)

    result = rewriter.rewrite("Does it have carve-outs?", HISTORY)

    assert result.rewritten is False
    assert result.source == "error"
    assert result.question == "Does it have carve-outs?"


# ---------------------------------------------------------------------------
# The suite that the CLI runs against a real model
# ---------------------------------------------------------------------------


def test_the_rewrite_suite_is_well_formed() -> None:
    from legal_lens.orchestrator.rewrite_suite import CASES

    assert len(CASES) >= 12
    assert len({case.id for case in CASES}) == len(CASES), "ids must be unique"

    for case in CASES:
        assert case.history, f"{case.id}: a rewrite case needs history to resolve against"
        assert case.why, f"{case.id}: say why the case matters, or it cannot be triaged"
        if case.expected == "no_rewrite":
            assert not case.must_contain, f"{case.id}: a decline has no rewrite to check"

    declines = [c for c in CASES if c.expected == "no_rewrite"]
    # Weighted towards declining on purpose: a missed rewrite costs a slightly worse
    # search, a wrong one costs a confident answer to a question nobody asked.
    assert len(declines) > len(CASES) / 2


@pytest.mark.parametrize("case_id", ["r14"])
def test_the_scope_creep_case_pins_the_dangerous_failure(case_id: str) -> None:
    from legal_lens.orchestrator.rewrite_suite import CASES

    case = next(c for c in CASES if c.id == case_id)
    # "What about indemnification?" after a question about a liability cap must not
    # inherit "cap" — that silently narrows what was asked.
    assert "cap" in case.must_not_contain
