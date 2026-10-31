"""Run the orchestration layer from the command line, with nothing else running.

No API, no database, no uploaded documents — the point of this package is that a
prompt can be tuned by running one command, and that is what this file is for.

Intent classification (the original behaviour, still the default)::

    uv run python -m legal_lens.orchestrator.cli "draft a memo on venue"
    uv run python -m legal_lens.orchestrator.cli --suite

The three workflows, over the generated eval corpus in `tests/eval/`::

    uv run python -m legal_lens.orchestrator.cli --index
    uv run python -m legal_lens.orchestrator.cli --ask "What is the liability cap?"
    uv run python -m legal_lens.orchestrator.cli --summary --doc msa
    uv run python -m legal_lens.orchestrator.cli --draft "memo on termination risk"
    uv run python -m legal_lens.orchestrator.cli --rewrite-suite

`--index` has to run once before the first `--ask` or `--draft`: it embeds the corpus
into an embedded Qdrant collection under `var/`. `--summary` reads the whole corpus
and needs no index.

Both suites report where the model disagrees with the expected outcome, which is the
quickest way to see whether a prompt edit helped or hurt.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

#: Messages with the route the prompt says they should take. The `expected=None` rows
#: are the ones that belong to no workflow; the prompt says those go to `ask` with low
#: confidence, so they are checked for the confidence signal rather than the label.
SUITE: list[tuple[str, str | None]] = [
    # --- plainly ask ---
    ("What is the indemnification cap?", "ask"),
    ("Does the MSA allow early termination?", "ask"),
    ("Is there an arbitration clause?", "ask"),
    ("What does the agreement say about force majeure?", "ask"),
    ("Does the amendment change the annual fee?", "ask"),
    ("Explain the liability carve-outs in clause 8.3.", "ask"),
    ("List every notice period in the agreement.", "ask"),
    ("Tell me the termination provisions.", "ask"),
    ("Is this non-compete enforceable?", "ask"),
    # --- plainly summary ---
    ("Summarise this agreement.", "summary"),
    ("Give me the key terms of the NDA.", "summary"),
    ("What are the main points of this contract?", "summary"),
    ("Brief me on the Arbor MSA.", "summary"),
    ("Could you possibly summarise this for me?", "summary"),
    # --- plainly draft ---
    ("Draft a memo on the enforceability of the non-compete.", "draft"),
    ("Write a clause capping liability at twelve months' fees.", "draft"),
    ("Prepare a letter to the counterparty about the late payment.", "draft"),
    ("Rewrite the indemnity to be mutual.", "draft"),
    ("Give me language for a tolling agreement.", "draft"),
    ("What would a mutual indemnity look like?", "draft"),
    # --- the boundary the prompt spends most of its words on ---
    ("Summarise the indemnification clause.", "ask"),
    ("Summarise what the amendment changed.", "ask"),
    # --- multi-intent: classify by terminal deliverable ---
    ("Summarise the MSA and then draft a memo on the termination risk.", "draft"),
    ("Read clause 9 and tell me if it's unusual.", "ask"),
    # --- long context, short request ---
    (
        "I'm reviewing this for a client who is worried about their exposure on the "
        "logistics side, and they've asked me to look specifically at what happens if "
        "the supplier misses a delivery window repeatedly over a quarter — so what "
        "does clause 4 say?",
        "ask",
    ),
    # --- none of the three: expect ask, and low confidence ---
    ("Hello", None),
    ("What can you do?", None),
    ("Delete the NDA from this matter.", None),
]


def _load_env() -> None:
    """Read `backend/.env` without requiring python-dotenv.

    Deliberately minimal: this module is meant to run with nothing else set up, and a
    five-line parser is better than a dependency for one file of `KEY=value`.
    """
    for candidate in (Path.cwd() / ".env", Path(__file__).parents[3] / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            # Never override something already exported in the shell.
            if key and key not in os.environ:
                os.environ[key] = value
        return


def _run_suite(classifier, verbose: bool) -> int:
    from .intent import FALLBACK_INTENT

    agree = 0
    checked = 0
    unscored: list[tuple[str, object]] = []
    disagreements: list[tuple[str, str | None, object]] = []

    print(f"model: {classifier.model}\n")
    for message, expected in SUITE:
        result = classifier.classify(message)
        if expected is None:
            unscored.append((message, result))
            mark = "·"
        else:
            checked += 1
            ok = result.intent == expected
            agree += ok
            mark = "✓" if ok else "✗"
            if not ok:
                disagreements.append((message, expected, result))

        note = ""
        if result.overridden:
            note = f"  (model said {result.model_intent}, floored to {result.intent})"
        if result.error:
            note = f"  ERROR {result.error[:60]}"
        print(
            f"  {mark} {result.intent:7} {result.confidence:4.2f} "
            f"{result.latency_ms:5}ms  {message[:56]}{note}"
        )
        if verbose:
            print(f"        {result.reasoning}")

    print(f"\nagreed with the expected route on {agree}/{checked}")

    if disagreements:
        print("\ndisagreements:")
        for message, expected, result in disagreements:
            print(f"  expected {expected}, got {result.intent} ({result.confidence:.2f})")
            print(f"    {message}")
            print(f"    model: {result.reasoning}")

    print("\nmessages that belong to no workflow (expect ask, low confidence):")
    for message, result in unscored:
        flag = "ok" if result.intent == FALLBACK_INTENT and result.confidence < 0.6 else "LOOK"
        print(f"  [{flag}] {result.intent:7} {result.confidence:4.2f}  {message}")
        print(f"         {result.reasoning}")

    return 0 if agree == checked else 1


# ---------------------------------------------------------------------------
# The three workflows
# ---------------------------------------------------------------------------


def _history(turns: list[str] | None):
    """Turn repeated `--turn` values into an alternating conversation.

    Attorney first, because that is how a conversation starts; an odd number ends on
    an attorney turn, which is also what a real history looks like mid-thread.
    """
    from .types import Turn

    if not turns:
        return []
    return [
        Turn(role="user" if i % 2 == 0 else "assistant", content=text)
        for i, text in enumerate(turns)
    ]


def _build(config, *, with_reranker: bool | None = None):
    """Encoders, index and retriever over the eval corpus.

    Loading the models takes seconds and printing nothing while it happens reads as a
    hang, so each stage announces itself.
    """
    from .fixtures import load
    from .retrieve import Encoders, PassageIndex, Retriever

    # `with_reranker=False` is for the paths that never rerank (indexing, summary);
    # everything else defers to the config flag.
    rerank = config.rerank_enabled if with_reranker is None else with_reranker
    print(f"loading {config.embed_model}", file=sys.stderr)
    if rerank:
        print(f"loading {config.rerank_model}", file=sys.stderr)
    encoders = Encoders(
        embed_model=config.embed_model,
        rerank_model=config.rerank_model,
        device=config.device,
        with_reranker=rerank,
        query_prompt=config.query_prompt,
    )
    store, passages = load()
    index = PassageIndex(config, encoders)
    return encoders, index, passages, Retriever(config, index, store, encoders)


def _cmd_index(config) -> int:
    import asyncio

    _, index, passages, _ = _build(config, with_reranker=False)
    index.drop()
    written = asyncio.run(index.index(passages))
    print(f"indexed {written} passages into {config.qdrant_collection}")
    print(f"  at {config.qdrant_path}")
    index.close()
    return 0


def _print_stream_header(label: str, model: str) -> None:
    print(f"\n── {label} · {model} " + "─" * max(0, 56 - len(label) - len(model)))


def _print_citations(citations) -> None:
    if not citations:
        print("\n(no citations)")
        return
    print("\ncitations:")
    for citation in citations:
        quoted = citation.cited_text.replace("\n", " ")
        if len(quoted) > 90:
            quoted = quoted[:90] + "…"
        print(f"  [{citation.n}] {citation.doc_id} p.{citation.page}  \u201c{quoted}\u201d")


def _print_usage(usage) -> None:
    if not usage:
        return
    print("\ncost:")
    for row in usage:
        # `cache_write` is printed because without it the numbers look broken: tokens
        # written to the cache are billed there and *excluded* from `input_tokens`,
        # so a 6,000-token prompt reports `in=16` on its first call.
        print(
            f"  {row.purpose:15} {row.model_id:20} "
            f"in={row.input_tokens:<6} out={row.output_tokens:<6} "
            f"cache_write={row.cache_write_tokens:<6} "
            f"cache_read={row.cache_read_tokens:<6} {row.latency_ms}ms"
        )


def _cmd_ask(config, question: str, doc_ids, history) -> int:
    import asyncio

    from .workflows import AskResult, AskWorkflow, CitationFound, TextDelta

    _, index, _, retriever = _build(config)
    workflow = AskWorkflow(config, retriever)

    async def run() -> AskResult | None:
        final = None
        _print_stream_header("ask", config.ask_model)
        async for event in workflow.run(question, history=history, doc_ids=doc_ids):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, CitationFound):
                pass  # printed together at the end, in order
            elif isinstance(event, AskResult):
                final = event
            else:  # Abstained
                print(event.message, end="", flush=True)
        print()
        return final

    result = asyncio.run(run())
    index.close()
    if result is None:
        return 1

    rewrite = result.rewrite
    if rewrite is not None and rewrite.source != "empty_history":
        print(f"\nrewrite    {rewrite.source}: {rewrite.reason}")
        if rewrite.rewritten:
            print(f"  searched  {result.searched_for}")
    print(
        f"\nretrieval  {result.candidates} candidates → {result.pages} pages  "
        f"coverage={result.coverage}"
    )
    print(f"grounded   {result.grounded}   abstained={result.abstained}")
    _print_citations(result.citations)
    _print_usage(result.usage)
    return 0


def _cmd_summary(config, doc_ids) -> int:
    import asyncio

    from .workflows import SummaryResult, SummaryWorkflow, TextDelta

    _, index, _, retriever = _build(config, with_reranker=False)
    workflow = SummaryWorkflow(config, retriever)

    async def run() -> SummaryResult | None:
        final = None
        print()
        async for event in workflow.run(doc_ids=doc_ids):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, SummaryResult):
                final = event
        print()
        return final

    result = asyncio.run(run())
    index.close()
    if result is None:
        return 1

    print(f"\nmodel      {result.model}")
    print(f"scope      {result.documents} documents, {result.pages} pages")
    print(f"retried    {result.retried}")
    print("\nfields:")
    for item in result.fields:
        state = "—" if item.value is None else f"{len(item.value)} chars"
        print(f"  {item.label:30} {state}")
    if result.missing:
        # Reported rather than hidden: a summary that dropped three of nine fields
        # looks complete and is not.
        print(f"\nstill empty after the retry: {', '.join(result.missing)}")
    _print_citations(result.citations)
    _print_usage(result.usage)
    return 0


def _cmd_draft(config, request: str, doc_ids) -> int:
    import asyncio

    from .workflows import MARKER, DraftResult, DraftWorkflow, TextDelta

    _, index, _, retriever = _build(config)
    workflow = DraftWorkflow(config, retriever)

    async def run() -> DraftResult | None:
        final = None
        _print_stream_header("draft", config.draft_model)
        async for event in workflow.run(request, doc_ids=doc_ids):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, DraftResult):
                final = event
        print()
        return final

    result = asyncio.run(run())
    index.close()
    if result is None:
        return 1

    print(f"\nretrieval  {result.pages} pages  coverage={result.coverage}")
    # FR-16/FR-17: the marker is the deliverable, so its count is the headline.
    print(f"markers    {result.markers}  ({MARKER})")
    _print_citations(result.citations)
    _print_usage(result.usage)
    return 0


def _run_rewrite_suite(config, verbose: bool) -> int:
    from .rewrite_suite import CASES
    from .workflows import QueryRewriter

    rewriter = QueryRewriter(config)
    agree = 0
    wrong_content: list[tuple[str, str, str]] = []
    disagreements = []

    print(f"model: {rewriter.model}\n")
    for case in CASES:
        result = rewriter.rewrite(case.question, case.history)
        verdict = "rewrite" if result.rewritten else "no_rewrite"
        ok = verdict == case.expected
        agree += ok
        mark = "\u2713" if ok else "\u2717"

        print(f"  {mark} {verdict:10} {case.id}  {case.question[:52]}")
        if verbose or not ok:
            print(f"        {result.reason}")
        if result.rewritten:
            print(f"        → {result.question}")

        if not ok:
            disagreements.append((case, result))
            continue

        # Content checks only where the verdict was right — a declined case has no
        # rewrite to inspect.
        if result.rewritten:
            lowered = result.question.lower()
            for term in case.must_contain:
                if term.lower() not in lowered:
                    wrong_content.append((case.id, f"missing {term!r}", result.question))
            for term in case.must_not_contain:
                if term.lower() in lowered:
                    # The invented-detail failure: the rewrite narrowed the question
                    # to something the attorney did not ask.
                    wrong_content.append((case.id, f"added {term!r}", result.question))

    print(f"\nverdict correct on {agree}/{len(CASES)}")

    if disagreements:
        print("\ndisagreements:")
        for case, result in disagreements:
            got = "rewrite" if result.rewritten else "no_rewrite"
            print(f"  {case.id}: expected {case.expected}, got {got}")
            print(f"    {case.question}")
            print(f"    why it matters: {case.why}")
            print(f"    model: {result.reason}")

    if wrong_content:
        print("\nverdict right, content wrong:")
        for case_id, problem, question in wrong_content:
            print(f"  {case_id}: {problem}")
            print(f"    {question}")

    return 0 if agree == len(CASES) and not wrong_content else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="legal_lens.orchestrator.cli",
        description="Classify a message as ask, summary or draft.",
    )
    parser.add_argument("message", nargs="*", help="the message to classify")
    parser.add_argument("--suite", action="store_true", help="run the built-in test set")
    parser.add_argument("--verbose", "-v", action="store_true", help="show reasoning")
    parser.add_argument("--model", default=None, help="override the model id")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="confidence below which the result is floored to ask",
    )
    parser.add_argument("--show-prompt", action="store_true", help="print the prompt and exit")

    workflows = parser.add_argument_group("workflows (over the tests/eval corpus)")
    workflows.add_argument(
        "--index", action="store_true", help="embed the eval corpus into Qdrant, then exit"
    )
    workflows.add_argument("--ask", metavar="QUESTION", help="run the ask workflow")
    workflows.add_argument("--summary", action="store_true", help="run the summary workflow")
    workflows.add_argument("--draft", metavar="REQUEST", help="run the draft workflow")
    workflows.add_argument(
        "--rewrite-suite", action="store_true", help="run the query-rewrite test set"
    )
    workflows.add_argument(
        "--doc",
        action="append",
        metavar="KEY",
        help="restrict scope to this document key (repeatable): msa, amendment, nda, …",
    )
    workflows.add_argument(
        "--turn",
        action="append",
        metavar="TEXT",
        help="prior conversation turn, alternating attorney/assistant (repeatable). "
        "Supply at least one to exercise the rewrite step.",
    )
    args = parser.parse_args(argv)

    if args.show_prompt:
        from .intent import load_prompt

        print(load_prompt())
        return 0

    _load_env()

    # The workflow commands come first: they share nothing with the classifier but
    # the environment loading above.
    if args.index or args.ask or args.summary or args.draft or args.rewrite_suite:
        from .config import OrchestratorConfig

        config = OrchestratorConfig()
        doc_ids = set(args.doc) if args.doc else None
        try:
            if args.index:
                return _cmd_index(config)
            if args.rewrite_suite:
                return _run_rewrite_suite(config, args.verbose)
            if args.ask:
                return _cmd_ask(config, args.ask, doc_ids, _history(args.turn))
            if args.summary:
                return _cmd_summary(config, doc_ids)
            if args.draft:
                return _cmd_draft(config, args.draft, doc_ids)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except ImportError as exc:
            print(
                f"error: {exc}\n"
                "The retrieval stack is an optional extra:\n"
                "  uv sync --extra orchestrator --extra workflows",
                file=sys.stderr,
            )
            return 2

    from .intent import DEFAULT_MIN_CONFIDENCE, DEFAULT_MODEL, IntentClassifier

    try:
        classifier = IntentClassifier(
            model=args.model or DEFAULT_MODEL,
            min_confidence=(
                args.min_confidence if args.min_confidence is not None else DEFAULT_MIN_CONFIDENCE
            ),
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.suite:
        return _run_suite(classifier, args.verbose)

    if not args.message:
        parser.print_help()
        return 2

    result = classifier.classify(" ".join(args.message))
    print(f"intent      {result.intent}")
    print(f"confidence  {result.confidence:.2f}")
    print(f"reasoning   {result.reasoning}")
    print(f"source      {result.source}")
    print(f"latency     {result.latency_ms}ms")
    print(f"tokens      in={result.input_tokens} out={result.output_tokens}")
    if result.overridden:
        print(f"note        model said {result.model_intent}, floored to {result.intent}")
    if result.error:
        print(f"error       {result.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
