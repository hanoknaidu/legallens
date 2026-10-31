"""Choosing `ask`, `summary` or `draft`.

The regex router is the default and the one the mock mirrors, so the first test here
is that enabling nothing changes nothing. The rest cover the model router: that it is
actually consulted, that its call is costed, and — the one that matters operationally
— that it degrades to the regex rather than failing the send when it cannot run.
"""

from __future__ import annotations

from helpers import NEW_CONVERSATION, ready_document
from legal_lens.config import PHASE1_OWNER_ID, Settings
from legal_lens.db.session import Database
from legal_lens.orchestrator.intent import IntentResult
from legal_lens.services import Services


class StubClassifier:
    """Stands in for the Haiku classifier."""

    model = "claude-haiku-4-5"

    def __init__(self, intent: str = "summary", *, tokens: bool = True) -> None:
        self._intent = intent
        self._tokens = tokens
        self.seen: list[str] = []

    def classify(self, message: str, *, history=None) -> IntentResult:
        self.seen.append(message)
        return IntentResult(
            intent=self._intent,  # type: ignore[arg-type]
            confidence=0.95,
            reasoning="stub",
            source="model",
            input_tokens=140 if self._tokens else 0,
            output_tokens=18 if self._tokens else 0,
            latency_ms=42,
        )


async def _services(tmp_path, **over) -> Services:
    settings = Settings(environment="test", data_dir=tmp_path, llm_provider="echo", **over)
    db = Database(settings.db_path)
    await db.connect()
    container = Services(db, settings)
    await container.bootstrap(PHASE1_OWNER_ID)
    return container


def _install(container: Services, classifier) -> None:
    """Inject the classifier past its lazy construction.

    Reaching into the private pair is deliberate: what is being tested is the
    dispatch and the costing, and building the real one needs a key and a network.
    """
    container._intent_classifier_built = True
    container._intent_classifier_instance = classifier


# ---------------------------------------------------------------------------


async def test_the_regex_router_is_the_default(tmp_path) -> None:
    container = await _services(tmp_path)
    try:
        assert container.settings.intent_router == "regex"
        assert await container.route_intent(PHASE1_OWNER_ID, "Summarise the MSA") == "summary"
        assert await container.route_intent(PHASE1_OWNER_ID, "Draft a memo") == "draft"
        assert await container.route_intent(PHASE1_OWNER_ID, "Where is venue?") == "ask"
    finally:
        await container.db.close()


async def test_the_regex_router_makes_no_model_call(tmp_path) -> None:
    """It has to stay free: it is what runs under `llm_provider='echo'`."""
    container = await _services(tmp_path)
    classifier = StubClassifier()
    _install(container, classifier)
    try:
        await container.route_intent(PHASE1_OWNER_ID, "Summarise the MSA")
        assert classifier.seen == []
    finally:
        await container.db.close()


async def test_the_model_router_decides_and_overrides_the_keywords(tmp_path) -> None:
    """The point of the model router: a phrasing the regex gets wrong.

    "What are the key obligations" has no keyword the regex matches, so it falls
    through to `ask`; the classifier reads it as a summary.
    """
    container = await _services(tmp_path, intent_router="model")
    classifier = StubClassifier("summary")
    _install(container, classifier)
    try:
        intent = await container.route_intent(
            PHASE1_OWNER_ID, "What are the key obligations under this contract?"
        )
        assert intent == "summary"
        assert classifier.seen == ["What are the key obligations under this contract?"]
    finally:
        await container.db.close()


async def test_the_routing_call_is_costed(tmp_path) -> None:
    """NFR-10: every model call is costed, including this one.

    The row carries no `audit_id` — the turn's audit row does not exist yet when the
    message is sent — and a cost with no parent is still a cost.
    """
    container = await _services(tmp_path, intent_router="model")
    _install(container, StubClassifier("draft"))
    try:
        await container.route_intent(PHASE1_OWNER_ID, "Prepare something for me")

        rows = await container.db.fetch_all(
            "SELECT purpose, model_id, input_tokens, audit_id FROM usage_costs"
        )
        assert len(rows) == 1
        assert rows[0]["purpose"] == "intent"
        assert rows[0]["model_id"] == "claude-haiku-4-5"
        assert rows[0]["input_tokens"] == 140
        assert rows[0]["audit_id"] is None
    finally:
        await container.db.close()


async def test_an_unmeasured_call_writes_no_cost_row(tmp_path) -> None:
    """A $0 row reads as "this was free" rather than "unmeasured"."""
    container = await _services(tmp_path, intent_router="model")
    _install(container, StubClassifier("ask", tokens=False))
    try:
        await container.route_intent(PHASE1_OWNER_ID, "Where is venue?")
        rows = await container.db.fetch_all("SELECT id FROM usage_costs")
        assert rows == []
    finally:
        await container.db.close()


async def test_an_unavailable_classifier_falls_back_to_the_regex(tmp_path) -> None:
    """Configured for the model router with no key available.

    A routing failure must not fail the send: a misrouted question still gets an
    answer, while a 500 loses the message the user typed. The classifier is built
    lazily and left as None, exactly as a missing generation key already degrades.
    """
    container = await _services(tmp_path, intent_router="model", anthropic_api_key=None)
    try:
        # Construction is attempted here and fails for want of a key.
        assert await container.route_intent(PHASE1_OWNER_ID, "Summarise the MSA") == "summary"
        assert container._intent_classifier_instance is None
        # And it is not retried on every message.
        assert await container.route_intent(PHASE1_OWNER_ID, "Draft a memo") == "draft"
    finally:
        await container.db.close()


async def test_the_classifier_is_built_once(tmp_path) -> None:
    container = await _services(tmp_path, intent_router="model")
    classifier = StubClassifier()
    _install(container, classifier)
    try:
        for _ in range(3):
            await container.route_intent(PHASE1_OWNER_ID, "Summarise")
        assert container._intent_classifier() is classifier
        assert len(classifier.seen) == 3
    finally:
        await container.db.close()


async def test_the_send_endpoint_returns_the_routed_intent(client) -> None:
    """The intent comes back on `POST /chat/message`, which the UI reads."""
    await ready_document(client)
    conversation = (await client.post("/conversations", json=NEW_CONVERSATION)).json()

    sent = await client.post(
        "/chat/message",
        json={"conversationId": conversation["id"], "content": "Summarise the agreement"},
    )

    assert sent.status_code == 200
    assert sent.json()["intent"] == "summary"
