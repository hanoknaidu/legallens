"""The intent classifier's logic, without spending a model call.

Everything here is the part that has to be right *when the model misbehaves* — an
empty message, a low-confidence answer, a malformed answer, a network failure. Those
paths are exactly the ones that never run during happy-path manual testing, and all
four of them resolve to `ask` on purpose.
"""

from __future__ import annotations

import pathlib

import pytest

from legal_lens.orchestrator.intent import (
    FALLBACK_INTENT,
    MAX_INPUT_CHARS,
    IntentClassifier,
    _Classification,
    load_prompt,
)


@pytest.fixture
def classifier() -> IntentClassifier:
    # A syntactically plausible key: the constructor needs one, and nothing here
    # reaches the network because `_chain` is replaced in each test that invokes.
    return IntentClassifier(api_key="sk-ant-test-not-real", min_confidence=0.35)


class _FakeChain:
    """Stands in for the LangChain structured-output chain."""

    def __init__(self, envelope: dict | None = None, raises: Exception | None = None) -> None:
        self._envelope = envelope
        self._raises = raises
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self._raises is not None:
            raise self._raises
        return self._envelope


def _envelope(intent: str, confidence: float, reasoning: str = "because") -> dict:
    return {
        "parsed": _Classification(intent=intent, confidence=confidence, reasoning=reasoning),
        "raw": None,
        "parsing_error": None,
    }


# ---------------------------------------------------------------------------
# The prompt is a deliverable in its own right
# ---------------------------------------------------------------------------


def test_the_prompt_ships_with_the_module_and_covers_the_three_workflows() -> None:
    prompt = load_prompt()

    assert len(prompt) > 2000, "the guide should be substantive, not a one-liner"
    for intent in ("`ask`", "`summary`", "`draft`"):
        assert intent in prompt

    # The boundary the prompt exists to resolve: summarising one clause is an ask,
    # not the nine-field overview.
    assert "Summarise the indemnification clause" in prompt
    # And the reason `ask` is the fallback has to be written down, or the next person
    # to edit the prompt will add a fourth category.
    assert "fails safely" in prompt


def test_the_nine_summary_fields_are_named_in_the_prompt() -> None:
    prompt = load_prompt()
    for field in (
        "Parties",
        "Term & renewal",
        "Obligations",
        "Payment terms",
        "Termination triggers",
        "Governing law & venue",
        "Liability & indemnification",
        "Unusual provisions",
        "Open items",
    ):
        assert field in prompt, f"missing summary field: {field}"


# ---------------------------------------------------------------------------
# The model is constrained by the schema, not by validation afterwards
# ---------------------------------------------------------------------------


def test_the_schema_admits_only_the_three_intents() -> None:
    from pydantic import ValidationError

    for intent in ("ask", "summary", "draft"):
        assert _Classification(intent=intent, confidence=1.0, reasoning="x").intent == intent

    # An off-menu intent is impossible by construction — the enum is in the tool
    # schema the model fills in, so it cannot answer "chitchat".
    with pytest.raises(ValidationError):
        _Classification(intent="chitchat", confidence=1.0, reasoning="x")


def test_confidence_is_bounded() -> None:
    from pydantic import ValidationError

    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            _Classification(intent="ask", confidence=bad, reasoning="x")


# ---------------------------------------------------------------------------
# Every failure mode resolves to `ask`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ["", "   ", "\n\t "])
def test_an_empty_message_is_not_sent_to_the_model(
    classifier: IntentClassifier, message: str
) -> None:
    chain = _FakeChain(_envelope("draft", 1.0))
    classifier._chain = chain

    result = classifier.classify(message)

    assert result.intent == FALLBACK_INTENT
    assert result.source == "empty_input"
    assert result.confidence == 0.0
    # No reason to pay for a call to discover the message was blank.
    assert chain.calls == []


def test_a_low_confidence_classification_is_floored_to_ask(
    classifier: IntentClassifier,
) -> None:
    classifier._chain = _FakeChain(_envelope("draft", 0.20, "guessing"))

    result = classifier.classify("hello there")

    # Routed safely...
    assert result.intent == FALLBACK_INTENT
    assert result.source == "low_confidence"
    # ...but what the model actually said is still reported, so the override is
    # visible rather than silent.
    assert result.model_intent == "draft"
    assert result.overridden is True
    assert result.confidence == 0.20
    assert result.reasoning == "guessing"


def test_a_confident_classification_is_respected(classifier: IntentClassifier) -> None:
    classifier._chain = _FakeChain(_envelope("draft", 0.95, "names a memo"))

    result = classifier.classify("draft a memo on venue")

    assert result.intent == "draft"
    assert result.source == "model"
    assert result.overridden is False


def test_confidence_exactly_at_the_floor_is_trusted(classifier: IntentClassifier) -> None:
    classifier._chain = _FakeChain(_envelope("summary", 0.35))

    # `>=`, not `>`: the floor is the lowest trusted value, and an off-by-one here
    # would silently reroute every borderline summary to ask.
    assert classifier.classify("key terms please").intent == "summary"


def test_a_model_failure_does_not_propagate(classifier: IntentClassifier) -> None:
    classifier._chain = _FakeChain(raises=RuntimeError("connection reset"))

    result = classifier.classify("summarise this agreement")

    # A routing failure must not take down the request. The caller gets a usable
    # answer and the reason it is not a real classification.
    assert result.intent == FALLBACK_INTENT
    assert result.source == "error"
    assert "connection reset" in (result.error or "")
    assert result.confidence == 0.0


def test_an_unparsable_answer_falls_back(classifier: IntentClassifier) -> None:
    classifier._chain = _FakeChain(
        {"parsed": None, "raw": None, "parsing_error": "no tool call returned"}
    )

    result = classifier.classify("what is the fee?")

    assert result.intent == FALLBACK_INTENT
    assert result.source == "error"
    assert "no tool call" in (result.error or "")


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_a_pasted_document_is_truncated_rather_than_sent_whole(
    classifier: IntentClassifier,
) -> None:
    chain = _FakeChain(_envelope("ask", 0.9))
    classifier._chain = chain

    classifier.classify("x" * (MAX_INPUT_CHARS * 3))

    sent = chain.calls[0][-1].content
    # Classification needs the request, not the document. Without this, pasting a
    # 50-page contract into the box costs a full-corpus classification call.
    assert len(sent) < MAX_INPUT_CHARS + 200


def test_history_is_accepted_but_not_yet_used(classifier: IntentClassifier) -> None:
    chain = _FakeChain(_envelope("ask", 0.9))
    classifier._chain = chain

    classifier.classify(
        "what about termination?",
        history=[{"role": "user", "content": "tell me about the MSA"}],
    )

    # The parameter exists so adding history later is not a signature change at every
    # call site. Feeding it in now would make the classifier answer a different
    # question than the one it is prompted for.
    assert len(chain.calls) == 1
    system, human = chain.calls[0]
    assert "tell me about the MSA" not in human.content


def test_the_model_is_haiku_by_default(classifier: IntentClassifier) -> None:
    # Named explicitly rather than inherited from a shared setting, so changing the
    # generation model cannot silently make routing expensive.
    assert classifier.model == "claude-haiku-4-5"


def test_the_standalone_copy_of_the_prompt_has_not_drifted() -> None:
    """`intent-classifier/` holds a duplicate of the prompt, and duplicates drift.

    The standalone folder exists so the classifier runs with nothing else installed,
    which means it cannot import this one — so the guide is copied rather than shared.
    The failure that follows is quiet: someone tunes the prompt in whichever copy they
    happen to have open, the suite passes there, and the other copy keeps the old
    behaviour. Nothing raises, and the two halves of the project disagree about how a
    message is routed.

    Compared byte for byte, because a difference in wording is the whole point: the
    prompt *is* the behaviour.
    """
    standalone = (
        pathlib.Path(__file__).resolve().parents[2] / "intent-classifier" / "intent_prompt.md"
    )
    if not standalone.is_file():  # pragma: no cover - backend checked out alone
        pytest.skip("the standalone intent-classifier folder is not present")

    canonical = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src"
        / "legal_lens"
        / "orchestrator"
        / "intent_prompt.md"
    )

    if standalone.read_text(encoding="utf-8") != canonical.read_text(encoding="utf-8"):
        import difflib

        diff = "\n".join(
            list(
                difflib.unified_diff(
                    canonical.read_text(encoding="utf-8").splitlines(),
                    standalone.read_text(encoding="utf-8").splitlines(),
                    fromfile="backend (canonical)",
                    tofile="intent-classifier (standalone)",
                    lineterm="",
                )
            )[:40]
        )
        raise AssertionError(
            "the two copies of intent_prompt.md have diverged, so the backend and the "
            "standalone classifier will route messages differently.\n\n"
            "Copy whichever you tuned over the other:\n"
            "  cp intent-classifier/intent_prompt.md "
            "backend/src/legal_lens/orchestrator/intent_prompt.md\n"
            "  # or the reverse\n\n" + diff
        )
