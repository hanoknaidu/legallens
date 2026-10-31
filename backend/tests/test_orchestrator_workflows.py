"""Ask, summary and draft, against a fake Anthropic stream.

Everything here is offline. The point is the machinery around the model call — block
assembly, citation resolution, abstention, heading parsing, the coverage guardrail —
because that is the part that fails silently. Whether the model obeys the prompts is
measured by the CLI against the eval corpus, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from legal_lens.orchestrator.citations import build_documents, cache_last, resolve
from legal_lens.orchestrator.config import OrchestratorConfig
from legal_lens.orchestrator.types import Page, Passage, Retrieved
from legal_lens.orchestrator.workflows import (
    ABSTAIN_SENTINEL,
    MARKER,
    Abstained,
    AskResult,
    AskWorkflow,
    DraftResult,
    DraftWorkflow,
    SummaryResult,
    SummaryWorkflow,
    TextDelta,
)
from legal_lens.orchestrator.workflows.ask import (
    FULL_COVERAGE_NOTICE,
    PARTIAL_COVERAGE_NOTICE,
    system_prompt,
)
from legal_lens.orchestrator.workflows.summary import HEADINGS, build_fields, parse_sections

PAGES = [
    Page(doc_id="msa", title="Master Services Agreement", page=1, text="Page one. Parties."),
    Page(doc_id="msa", title="Master Services Agreement", page=2, text="Page two. Indemnity."),
    Page(doc_id="nda", title="Mutual NDA", page=1, text="NDA page one. Three-year term."),
]


# ---------------------------------------------------------------------------
# A fake Anthropic streaming client
# ---------------------------------------------------------------------------


@dataclass
class FakeCitation:
    document_index: int
    start_block_index: int
    cited_text: str
    type: str = "content_block_location"


@dataclass
class FakeDelta:
    type: str
    text: str = ""
    citation: Any = None


@dataclass
class FakeEvent:
    type: str
    delta: Any


class FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 1200
        self.output_tokens = 300
        self.cache_read_input_tokens = 1000
        self.cache_creation_input_tokens = 0


class FakeFinal:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = FakeUsage()


class FakeStream:
    def __init__(self, events, final) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def __aiter__(self):
        async def events():
            for event in self._events:
                yield event

        return events()

    async def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, script) -> None:
        self._script = list(script)
        self.requests: list[dict] = []

    def stream(self, **kwargs):
        self.requests.append(kwargs)
        events, final = self._script.pop(0)
        return FakeStream(events, final)


class FakeClient:
    """Replays one scripted response per call."""

    def __init__(self, *responses) -> None:
        self.messages = FakeMessages(responses)


def text_events(*chunks: str) -> list[FakeEvent]:
    return [
        FakeEvent("content_block_delta", FakeDelta("text_delta", text=chunk)) for chunk in chunks
    ]


def citation_event(document_index: int, block: int, quote: str) -> FakeEvent:
    return FakeEvent(
        "content_block_delta",
        FakeDelta("citations_delta", citation=FakeCitation(document_index, block, quote)),
    )


def block_stop() -> FakeEvent:
    """Closes a content block.

    Worth scripting faithfully: the API sends a block's `citations_delta` *before* its
    text, so this event is what tells the stream that the sentence a citation belongs
    to is finished and its marker can follow it. A script without it produces markers
    that are all correct and all in the wrong place.
    """
    return FakeEvent("content_block_stop", None)


# ---------------------------------------------------------------------------
# A retriever that returns what the test asks for
# ---------------------------------------------------------------------------


class FakeRetriever:
    """Returns what the test asks for, on whichever arm the test wants.

    `select()` is the real retriever's route decision, which these tests do not
    exercise: they pin the *retrieval* arm, so by default `select()` delegates to
    `search()` and `searched` stays the record of what was looked for. The decision
    itself is tested against the real `Retriever` and a real `PageStore`, in
    `test_orchestrator_retrieval.py`, where a corpus has a size to be measured.

    `route="whole_document"` makes it take the other arm, for the tests that care
    that a workflow honours the route rather than always searching.
    """

    def __init__(self, pages: list[Page], *, corpus_pages: int, route: str = "rag") -> None:
        self._pages = pages
        self._corpus_pages = corpus_pages
        self._route = route
        self.searched: list[str] = []
        self.selected: list[str] = []

    async def select(self, question, *, doc_ids=None, top_n=None) -> Retrieved:
        self.selected.append(question)
        if self._route == "whole_document":
            return await self.whole(doc_ids)
        return await self.search(question, doc_ids=doc_ids, top_n=top_n)

    async def search(self, question, *, doc_ids=None, top_n=None) -> Retrieved:
        self.searched.append(question)
        return Retrieved(
            pages=self._pages,
            candidates=25,
            kept=len(self._pages),
            corpus_pages=self._corpus_pages,
            path="rag",
        )

    async def whole(self, doc_ids=None) -> Retrieved:
        return Retrieved(
            pages=self._pages,
            candidates=len(self._pages),
            kept=len(self._pages),
            corpus_pages=self._corpus_pages,
            path="whole_document",
        )


class NoRewrite:
    """A rewriter that always declines, so ask/draft tests isolate retrieval."""

    def rewrite(self, question, history=None):
        from legal_lens.orchestrator.workflows.rewrite import RewriteResult

        return RewriteResult(
            question=question,
            original=question,
            rewritten=False,
            reason="stub",
            source="empty_history",
        )


def _config(**over) -> OrchestratorConfig:
    return OrchestratorConfig(api_key="test-key", **over)


async def _collect(workflow_run):
    events = []
    final = None
    async for event in workflow_run:
        if isinstance(event, AskResult | SummaryResult | DraftResult):
            final = event
        else:
            events.append(event)
    return events, final


# ---------------------------------------------------------------------------
# Document blocks and citation resolution
# ---------------------------------------------------------------------------


def test_pages_group_into_one_document_block_each() -> None:
    blocks, index = build_documents(PAGES)

    assert len(blocks) == 2, "two source documents, two document blocks"
    assert [b["title"] for b in blocks] == ["Master Services Agreement", "Mutual NDA"]
    # All-or-nothing: a mix of cited and uncited document blocks is rejected by the API.
    assert all(b["citations"] == {"enabled": True} for b in blocks)
    assert len(blocks[0]["source"]["content"]) == 2
    assert index[0][1].page == 2


def test_a_citation_resolves_to_the_page_its_block_came_from() -> None:
    _, index = build_documents(PAGES)

    citation = resolve(FakeCitation(0, 1, "Indemnity"), index, 1)

    assert citation is not None
    # Computed from the block position, not reported by the model — which is what
    # makes the page correct by construction.
    assert (citation.doc_id, citation.page) == ("msa", 2)
    assert citation.cited_text == "Indemnity"


def test_an_out_of_range_citation_is_dropped_rather_than_guessed() -> None:
    _, index = build_documents(PAGES)

    # A dropped chip is a visible absence; a chip on the wrong page is a claim the
    # attorney has no way to check.
    assert resolve(FakeCitation(0, 99, "x"), index, 1) is None
    assert resolve(FakeCitation(99, 0, "x"), index, 1) is None
    assert resolve(FakeCitation(0, 0, ""), index, 1) is None


def test_the_cache_breakpoint_lands_on_the_last_document() -> None:
    blocks, _ = build_documents(PAGES)

    cached = cache_last(blocks)

    assert "cache_control" not in cached[0]
    assert cached[-1]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


async def test_ask_streams_text_and_resolves_citations() -> None:
    client = FakeClient(
        (
            [
                # The real event order: a block's citation arrives before its text,
                # and the marker follows the text once the block closes.
                citation_event(0, 1, "Indemnity"),
                *text_events("The Supplier ", "indemnifies the Customer."),
                block_stop(),
            ],
            FakeFinal(),
        )
    )
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    events, result = await _collect(workflow.run("What are the indemnity obligations?"))

    # The prose arrives with a `{{cite:1}}` marker where the citation landed. That
    # marker is the whole of the chip feature: the renderer has no other way to know
    # *which* claim a source supports, and the "N sources" badge points nowhere.
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == (
        "The Supplier indemnifies the Customer.{{cite:1}}"
    )
    assert result is not None
    assert [c.page for c in result.citations] == [2]
    assert result.grounded is True
    assert result.abstained is False


async def test_a_span_cited_twice_is_one_source_with_two_markers() -> None:
    # The model supports two separate sentences with the same passage. One chip
    # belongs in the list; both sentences need a way back to it.
    client = FakeClient(
        (
            [
                citation_event(0, 1, "Indemnity"),
                *text_events("The Supplier indemnifies."),
                block_stop(),
                citation_event(0, 1, "Indemnity"),
                *text_events(" That duty survives."),
                block_stop(),
            ],
            FakeFinal(),
        )
    )
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    events, result = await _collect(workflow.run("Who indemnifies whom?"))

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "The Supplier indemnifies.{{cite:1}} That duty survives.{{cite:1}}"
    assert result is not None
    assert len(result.citations) == 1, "a repeat is one source, not two"


async def test_a_summary_carries_no_inline_markers() -> None:
    # The summary's text is parsed back into nine headed sections, and each field
    # carries its own `cites`. A marker here would be parsed into the field's value
    # and rendered to the attorney as literal braces.
    # Two scripted responses: one heading answered leaves eight missing, so the
    # workflow spends its single retry before yielding.
    client = FakeClient(
        (
            [
                citation_event(0, 0, "Page one. Parties."),
                *text_events("## Parties\nNorthwind and Arbor.\n"),
                block_stop(),
            ],
            FakeFinal(),
        ),
        (text_events("## Term & renewal\nNOT PRESENT\n"), FakeFinal()),
    )
    workflow = SummaryWorkflow(_config(), FakeRetriever(PAGES, corpus_pages=3), client=client)

    _, result = await _collect(workflow.run())

    assert result is not None
    parties = next(f for f in result.fields if f.key == "parties")
    assert parties.value == "Northwind and Arbor."
    assert "{{cite:" not in (parties.value or "")
    assert parties.cites == [1]


async def test_ask_holds_back_the_abstention_sentinel() -> None:
    # Streamed in pieces, so the naive implementation flashes "NOT_IN_" into the
    # transcript before retracting it.
    client = FakeClient((text_events("NOT_", "IN_", "DOCUMENTS"), FakeFinal()))
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    events, result = await _collect(workflow.run("Is there an arbitration clause?"))

    assert not [e for e in events if isinstance(e, TextDelta)]
    assert [e for e in events if isinstance(e, Abstained)]
    assert result is not None and result.abstained is True
    assert ABSTAIN_SENTINEL not in result.answer
    # FR-9: abstention is a correct outcome, judged grounded on its own terms.
    assert result.grounded is True


async def test_an_uncited_answer_is_marked_ungrounded() -> None:
    client = FakeClient((text_events("Probably thirty days."), FakeFinal()))
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    _, result = await _collect(workflow.run("What is the notice period?"))

    # NFR-1: a factual assertion with no citation is ungrounded by definition.
    assert result is not None and result.grounded is False


async def test_a_safety_refusal_is_reported_as_an_abstention() -> None:
    client = FakeClient(([], FakeFinal(stop_reason="refusal")))
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    events, result = await _collect(workflow.run("anything"))

    # Otherwise the UI hangs or shows a raw error for what is a known outcome.
    assert [e for e in events if isinstance(e, Abstained)]
    assert result is not None and result.abstained is True


async def test_no_pages_in_scope_abstains_without_calling_the_model() -> None:
    client = FakeClient()
    workflow = AskWorkflow(
        _config(), FakeRetriever([], corpus_pages=0), rewriter=NoRewrite(), client=client
    )

    events, result = await _collect(workflow.run("anything"))

    assert client.messages.requests == []
    assert [e for e in events if isinstance(e, Abstained)]
    assert result is not None and result.abstained is True


async def test_the_rewritten_question_is_what_retrieval_searches_for() -> None:
    from legal_lens.orchestrator.workflows.rewrite import RewriteResult

    class Rewriter:
        def rewrite(self, question, history=None):
            return RewriteResult(
                question="What are the carve-outs to the cap in the Arbor MSA?",
                original=question,
                rewritten=True,
                reason="resolved 'it'",
                source="model",
            )

    retriever = FakeRetriever(PAGES, corpus_pages=3)
    client = FakeClient((text_events("x"), FakeFinal()))
    workflow = AskWorkflow(_config(), retriever, rewriter=Rewriter(), client=client)

    _, result = await _collect(workflow.run("Does it have carve-outs?"))

    assert retriever.searched == ["What are the carve-outs to the cap in the Arbor MSA?"]
    assert result is not None
    assert result.question == "Does it have carve-outs?", "the original survives for audit"
    assert result.searched_for != result.question
    # The question sent to the model is the rewrite, not the original.
    sent = client.messages.requests[0]["messages"][0]["content"][-1]["text"]
    assert sent == "What are the carve-outs to the cap in the Arbor MSA?"


# ---------------------------------------------------------------------------
# The coverage guardrail — the "always retrieve" safety valve
# ---------------------------------------------------------------------------


def test_full_coverage_permits_an_absence_claim() -> None:
    prompt = system_prompt(coverage=True)

    assert FULL_COVERAGE_NOTICE in prompt
    assert PARTIAL_COVERAGE_NOTICE not in prompt
    assert "{coverage_notice}" not in prompt


def test_partial_coverage_forbids_an_absence_claim() -> None:
    prompt = system_prompt(coverage=False)

    # Retrieval ran over a corpus bigger than what reached the prompt, so "the
    # contract is silent on arbitration" would be a guess dressed as a finding.
    assert "Never state that the documents are silent" in prompt
    assert FULL_COVERAGE_NOTICE not in prompt


@pytest.mark.parametrize(
    ("pages", "corpus_pages", "expected"),
    [
        (3, 3, True),  # retrieval returned everything: an absence is provable
        (3, 40, False),  # a selection: it is not
        (0, 0, False),  # nothing in scope at all
    ],
)
def test_coverage_is_computed_from_what_reached_the_prompt(
    pages: int, corpus_pages: int, expected: bool
) -> None:
    retrieved = Retrieved(pages=PAGES[:pages], corpus_pages=corpus_pages)

    assert retrieved.coverage is expected


async def test_a_partial_corpus_sends_the_restrictive_prompt_and_message() -> None:
    client = FakeClient((text_events(ABSTAIN_SENTINEL), FakeFinal()))
    workflow = AskWorkflow(
        _config(),
        FakeRetriever(PAGES, corpus_pages=200),
        rewriter=NoRewrite(),
        client=client,
    )

    events, result = await _collect(workflow.run("Is there an arbitration clause?"))

    system = client.messages.requests[0]["system"][0]["text"]
    assert "Never state that the documents are silent" in system

    message = next(e for e in events if isinstance(e, Abstained)).message
    # The user-facing wording has to match what was actually proved.
    assert "excerpts" in message
    assert "not a finding that the documents are silent" in message.lower()
    assert result is not None and result.coverage is False


# ---------------------------------------------------------------------------
# Request shape — the model constraints that are 400s
# ---------------------------------------------------------------------------


async def test_the_request_omits_thinking_and_sets_effort_explicitly() -> None:
    client = FakeClient((text_events("x"), FakeFinal()))
    workflow = AskWorkflow(
        _config(ask_effort="low"),
        FakeRetriever(PAGES, corpus_pages=3),
        rewriter=NoRewrite(),
        client=client,
    )

    await _collect(workflow.run("q"))
    request = client.messages.requests[0]

    # `thinking: {"type": "disabled"}` is a 400 on Sonnet 5.5 and cannot be disabled
    # at all on Opus 5.5, so the parameter is never sent; effort is the only control.
    assert "thinking" not in request
    assert request["output_config"] == {"effort": "low"}
    assert request["model"] == "claude-sonnet-5-5"
    # Citations are incompatible with structured outputs.
    assert "format" not in request.get("output_config", {})
    assert "tool_choice" not in request


async def test_the_documents_precede_the_question_so_caching_can_work() -> None:
    client = FakeClient((text_events("x"), FakeFinal()))
    workflow = AskWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=3), rewriter=NoRewrite(), client=client
    )

    await _collect(workflow.run("the question"))
    content = client.messages.requests[0]["messages"][0]["content"]

    # Caching is prefix-matched, so the large stable part has to come first.
    assert [b["type"] for b in content] == ["document", "document", "text"]
    assert content[-1]["text"] == "the question"
    assert content[1]["cache_control"] == {"type": "ephemeral"}
    assert client.messages.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


async def test_only_ask_pays_to_cache_its_documents() -> None:
    """A cache write with no reader is a 25% surcharge, not a saving.

    Measured on a five-turn run, cache writes were **62.6% of total spend** — the
    same ~20K document prefix written once per workflow, and two of the three writes
    read zero times. `ask` reads its own on the next turn of the conversation;
    summary and draft produce one artifact from one call and never come back.

    This is a cost property with no visible symptom, which is exactly the kind that
    creeps back. The system prompt stays cached in all three: it is small, and it is
    byte-identical on every call of its workflow.
    """
    # ask — written, because the next turn of the conversation reads it.
    client = FakeClient((text_events("x"), FakeFinal()))
    await _collect(
        AskWorkflow(
            _config(),
            FakeRetriever(PAGES, corpus_pages=3),
            rewriter=NoRewrite(),
            client=client,
        ).run("q")
    )
    docs = [
        b for b in client.messages.requests[0]["messages"][0]["content"] if b["type"] == "document"
    ]
    assert "cache_control" in docs[-1], "ask should cache: a conversation re-reads it"

    # draft — not written. One call, no retry pass, no reader.
    client = FakeClient((text_events("A memo."), FakeFinal()))
    await _collect(
        DraftWorkflow(
            _config(),
            FakeRetriever(PAGES, corpus_pages=3),
            rewriter=NoRewrite(),
            client=client,
        ).run("draft a memo")
    )
    docs = [
        b for b in client.messages.requests[0]["messages"][0]["content"] if b["type"] == "document"
    ]
    assert all("cache_control" not in b for b in docs), "draft has no second reader"
    assert client.messages.requests[0]["system"][0]["cache_control"] == {"type": "ephemeral"}

    # summary — not written either; see `summary.CACHE_DOCUMENTS` for the break-even.
    client = FakeClient((text_events(_nine_headings()), FakeFinal()))
    await _collect(
        SummaryWorkflow(_config(), FakeRetriever(PAGES, corpus_pages=3), client=client).run()
    )
    docs = [
        b for b in client.messages.requests[0]["messages"][0]["content"] if b["type"] == "document"
    ]
    assert all("cache_control" not in b for b in docs), "summary retries too rarely"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _nine_headings(skip: set[str] = frozenset()) -> str:
    return "\n\n".join(
        f"## {label}\nContent for {label}." for _, label in HEADINGS if label not in skip
    )


def test_all_nine_headings_parse() -> None:
    fields = build_fields(_nine_headings(), [])

    assert [f.key for f in fields] == [key for key, _ in HEADINGS]
    assert all(f.value for f in fields)


def test_a_skipped_heading_and_an_explicit_absence_are_both_null() -> None:
    text = _nine_headings(skip={"Open items"}) + "\n\n## Unusual provisions\nNOT PRESENT"

    fields = {f.label: f for f in build_fields(text, [])}

    # FR-13: the field must exist and the absence must be explicit either way, so the
    # renderer has something to show.
    assert fields["Open items"].value is None
    assert fields["Unusual provisions"].value is None
    assert fields["Parties"].value is not None


def test_headings_are_matched_regardless_of_case() -> None:
    sections = parse_sections("## GOVERNING LAW & VENUE\nDelaware.")

    # A model that wrote the heading in title case did what was asked and should not
    # lose the field to a capital letter.
    assert sections["governing law & venue"] == "Delaware."


def test_an_absent_field_claims_no_citations() -> None:
    from legal_lens.orchestrator.types import Citation

    citations = [Citation(n=1, doc_id="msa", doc_title="MSA", page=1, cited_text="x")]
    text = "## Parties\nTwo.\n\n## Open items\nNOT PRESENT"
    fields = {f.label: f for f in build_fields(text, citations)}

    assert fields["Parties"].cites == [1]
    assert fields["Open items"].cites == []


async def test_summary_reads_every_page_rather_than_retrieving() -> None:
    retriever = FakeRetriever(PAGES, corpus_pages=3)
    client = FakeClient((text_events(_nine_headings()), FakeFinal()))
    workflow = SummaryWorkflow(_config(), retriever, client=client)

    _, result = await _collect(workflow.run())

    # A summary of retrieved fragments is not a summary, and *Open items* is an
    # absence question by construction.
    assert retriever.searched == []
    assert result is not None and result.pages == 3
    assert not result.missing


async def test_summary_retries_once_for_missing_headings_and_merges() -> None:
    first = _nine_headings(skip={"Open items", "Unusual provisions"})
    second = "## Open items\nUnexecuted signature block.\n\n## Unusual provisions\nNOT PRESENT"
    client = FakeClient((text_events(first), FakeFinal()), (text_events(second), FakeFinal()))
    workflow = SummaryWorkflow(_config(), FakeRetriever(PAGES, corpus_pages=3), client=client)

    _, result = await _collect(workflow.run())

    assert result is not None and result.retried is True
    assert len(client.messages.requests) == 2
    fields = {f.label: f for f in result.fields}
    assert fields["Open items"].value == "Unexecuted signature block."
    # An honest absence after a direct second ask stays absent.
    assert fields["Unusual provisions"].value is None
    # And the first pass's fields were not overwritten.
    assert fields["Parties"].value is not None
    assert len(result.usage) == 2, "both calls are costed separately"


async def test_a_complete_summary_does_not_retry() -> None:
    client = FakeClient((text_events(_nine_headings()), FakeFinal()))
    workflow = SummaryWorkflow(_config(), FakeRetriever(PAGES, corpus_pages=3), client=client)

    _, result = await _collect(workflow.run())

    assert result is not None and result.retried is False
    assert len(client.messages.requests) == 1


def test_summary_routes_to_the_larger_model_for_reconciliation() -> None:
    config = _config()
    workflow = SummaryWorkflow(config, FakeRetriever(PAGES, corpus_pages=3), client=FakeClient())

    one_document = [PAGES[0], PAGES[1]]
    assert workflow.model_for(one_document) == "claude-sonnet-5-5"
    # Two documents: reconciliation is possible, so the stronger model earns its price.
    assert workflow.model_for(PAGES) == "claude-opus-5-5"

    amendment = [Page(doc_id="a", title="Amendment No. 2 to the MSA", page=1, text="x")]
    # One document, but the hard case: it contradicts the agreement it amends.
    assert workflow.model_for(amendment) == "claude-opus-5-5"


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


async def test_draft_counts_the_attorney_input_markers() -> None:
    body = f"# Memorandum\n\nThe term runs for {MARKER} years, terminable on {MARKER} days' notice."
    client = FakeClient((text_events(body), FakeFinal()))
    workflow = DraftWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=200), rewriter=NoRewrite(), client=client
    )

    _, result = await _collect(workflow.run("memo on the term"))

    # FR-16/FR-17: the marker is the deliverable, so its count is a first-class
    # property of the result rather than something to grep for later.
    assert result is not None and result.markers == 2
    assert MARKER in result.body


async def test_draft_uses_the_wider_keep_and_the_larger_model() -> None:
    client = FakeClient((text_events("body"), FakeFinal()))
    config = _config()
    workflow = DraftWorkflow(
        config, FakeRetriever(PAGES, corpus_pages=200), rewriter=NoRewrite(), client=client
    )

    await _collect(workflow.run("memo"))
    request = client.messages.requests[0]

    assert request["model"] == "claude-opus-5-5"
    # Opus 5.5 defaults to `medium`, so omitting this would silently buy less.
    assert request["output_config"] == {"effort": "high"}
    assert request["max_tokens"] == config.draft_max_output_tokens
    assert config.draft_top_n > config.ask_top_n


async def test_a_partial_corpus_adds_the_restrictive_notice_to_the_draft_prompt() -> None:
    client = FakeClient((text_events("body"), FakeFinal()))
    workflow = DraftWorkflow(
        _config(), FakeRetriever(PAGES, corpus_pages=200), rewriter=NoRewrite(), client=client
    )

    await _collect(workflow.run("memo"))

    system = client.messages.requests[0]["system"][0]["text"]
    assert "never describe a provision as absent" in system.lower()


def test_the_title_keeps_the_documents_own_capitalisation() -> None:
    from legal_lens.orchestrator.workflows.draft import title_for

    # `.title()` mangles "MSA" into "Msa" and "non-compete" into "Non-Compete".
    assert title_for("memo on the MSA non-compete") == "Memo on the MSA non-compete"
    assert title_for("") == "Draft"


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        # The imperative goes; the noun phrase stays. A request and a title are
        # different things, and the rendered memo shows the title.
        ("Draft a short memo on the termination provisions", "Memo on the termination provisions"),
        ("Please prepare a brief note regarding data residency", "Note regarding data residency"),
        ("Give me a one-page summary of the fee escalator", "Summary of the fee escalator"),
        # Longest-first alternation: `memo|memorandum` would leave "randum", and
        # `a|an` would leave "n email".
        (
            "draft a comprehensive memorandum on the MSA termination rights",
            "Memorandum on the MSA termination rights",
        ),
        (
            "Prepare an email to the counterparty on the renewal notice",
            "Email to the counterparty on the renewal notice",
        ),
        # The document noun survives. An earlier rule also ate the noun and the
        # preposition after it, which lost what the document was.
        ("Write me a clause that caps liability", "Clause that caps liability"),
        # Nothing to strip: not every request opens with an imperative.
        (
            "Summarise the governing law provisions",
            "Summarise the governing law provisions",
        ),
        # Stripping would leave nothing worth reading, so the request stands.
        ("Draft", "Draft"),
        ("  a tolling agreement?  ", "A tolling agreement"),
    ],
)
def test_the_title_names_a_document_not_the_request(request_text: str, expected: str) -> None:
    from legal_lens.orchestrator.workflows.draft import title_for

    assert title_for(request_text) == expected


# ---------------------------------------------------------------------------
# Page expansion
# ---------------------------------------------------------------------------


def test_a_passage_spanning_two_pages_contributes_both() -> None:
    from legal_lens.orchestrator.retrieve import covered_pages

    pages = covered_pages(
        [
            Passage(doc_id="msa", title="MSA", page_start=4, page_end=5, text="x"),
            Passage(doc_id="msa", title="MSA", page_start=5, page_end=5, text="y"),
            Passage(doc_id="nda", title="NDA", page_start=1, page_end=1, text="z"),
        ]
    )

    # The cited span could be on either page of a chunk that straddles a boundary,
    # and page 5 appearing in two passages must not duplicate it.
    assert pages == [("msa", 4), ("msa", 5), ("nda", 1)]
