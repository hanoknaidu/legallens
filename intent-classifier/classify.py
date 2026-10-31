#!/usr/bin/env python3
"""Standalone intent classifier — ask, summary or draft.

Self-contained on purpose: no `legal_lens` package, no FastAPI, no database, no uv.
A virtualenv, one dependency, and a key in `.env` beside this file.

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    #  put your key in .env
    python3 classify.py "draft a memo on venue"
    python3 classify.py --suite

The classification guide is `intent_prompt.md` in this folder — plain Markdown, edit
it and re-run the suite to see what changed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from typing import Literal

HERE = pathlib.Path(__file__).parent
PROMPT = HERE / "intent_prompt.md"
ENV = HERE / ".env"

Intent = Literal["ask", "summary", "draft"]

MODEL = "claude-haiku-4-5"
#: Of the three workflows only `ask` can conclude "the documents do not address this"
#: and stop. `summary` asserts nine fields; `draft` writes a document the attorney may
#: sign. So every failure — empty input, low confidence, a bad answer, a network error
#: — resolves here, because that is the one that fails safely.
FALLBACK: Intent = "ask"
MIN_CONFIDENCE = 0.35
MAX_INPUT_CHARS = 4000


def load_env() -> None:
    """Read `.env` beside this file. Deliberately not python-dotenv — one dependency."""
    if not ENV.is_file():
        return
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip quotes: a pasted key often carries them, and the SDK sends the value
        # verbatim, so a stray quote is a 401 that looks like a bad key.
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def build_chain():
    from langchain_anthropic import ChatAnthropic
    from pydantic import BaseModel, Field

    class Classification(BaseModel):
        """Route this message to a workflow."""

        intent: Intent = Field(
            description=(
                "Which workflow should handle this message. 'ask' answers a question "
                "about what the documents say. 'summary' produces the fixed nine-field "
                "overview. 'draft' produces new prose the attorney will edit and send."
            )
        )
        confidence: float = Field(
            ge=0.0, le=1.0,
            description=(
                "How confident you are, 0 to 1. Be honest rather than generous: a low "
                "score on an ambiguous message is more useful than a high score on a "
                "guess."
            ),
        )
        reasoning: str = Field(description="One sentence naming the signal you used.")

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit(
            f"error: no ANTHROPIC_API_KEY.\n"
            f"  Put it in {ENV} on the ANTHROPIC_API_KEY= line, or export it."
        )
    if not key.startswith("sk-ant-"):
        sys.exit(
            f"error: that does not look like an Anthropic key (starts {key[:11]}…).\n"
            f"  A secret starts 'sk-ant-'. A value starting 'apikey_' is the key's ID\n"
            f"  from the console list, not the key itself."
        )

    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    model = ChatAnthropic(
        model=MODEL,
        api_key=key,
        # Routing the same message two ways on two days is a bug, not variety.
        temperature=0,
        max_tokens=512,
        timeout=20.0,
        # A key not scoped to a workspace authenticates and then fails every request.
        default_headers=({"anthropic-workspace-id": workspace} if workspace else None),
    )
    # The enum lives in the tool schema, so an off-menu answer is impossible by
    # construction rather than caught by validation afterwards.
    return model.with_structured_output(
        Classification, method="function_calling", include_raw=True
    )


def classify(chain, message: str) -> dict:
    text = (message or "").strip()
    if not text:
        return {"intent": FALLBACK, "confidence": 0.0, "source": "empty_input",
                "reasoning": "The message is empty.", "ms": 0}
    if len(text) > MAX_INPUT_CHARS:
        # Classification needs the request, not a pasted document.
        text = text[:MAX_INPUT_CHARS]

    started = time.perf_counter()
    try:
        envelope = chain.invoke(
            [("system", PROMPT.read_text(encoding="utf-8")),
             ("human", f"Classify this message:\n\n{text}")]
        )
    except Exception as exc:
        return {"intent": FALLBACK, "confidence": 0.0, "source": "error",
                "reasoning": "Classification failed; routed to the safe default.",
                "error": f"{type(exc).__name__}: {exc}",
                "ms": int((time.perf_counter() - started) * 1000)}

    ms = int((time.perf_counter() - started) * 1000)
    parsed = envelope.get("parsed")
    if parsed is None:
        return {"intent": FALLBACK, "confidence": 0.0, "source": "error",
                "reasoning": "The model did not return a classification.", "ms": ms}

    trusted = parsed.confidence >= MIN_CONFIDENCE
    return {
        "intent": parsed.intent if trusted else FALLBACK,
        "model_intent": parsed.intent,
        "confidence": parsed.confidence,
        "reasoning": parsed.reasoning,
        "source": "model" if trusted else "low_confidence",
        "ms": ms,
    }


SUITE: list[tuple[str, str | None]] = [
    ("What is the indemnification cap?", "ask"),
    ("Is there an arbitration clause?", "ask"),
    ("Does the amendment change the annual fee?", "ask"),
    ("Explain the liability carve-outs in clause 8.3.", "ask"),
    ("Tell me the termination provisions.", "ask"),
    ("Is this non-compete enforceable?", "ask"),
    ("Summarise this agreement.", "summary"),
    ("Give me the key terms of the NDA.", "summary"),
    ("Could you possibly summarise this for me?", "summary"),
    ("Draft a memo on the enforceability of the non-compete.", "draft"),
    ("Write a clause capping liability at twelve months' fees.", "draft"),
    ("Rewrite the indemnity to be mutual.", "draft"),
    ("What would a mutual indemnity look like?", "draft"),
    # The boundary the prompt spends most of its words on.
    ("Summarise the indemnification clause.", "ask"),
    ("Summarise what the amendment changed.", "ask"),
    # Multi-intent: classify by the terminal deliverable.
    ("Summarise the MSA and then draft a memo on the termination risk.", "draft"),
    # Belongs to no workflow: expect ask, and low confidence.
    ("Hello", None),
    ("What can you do?", None),
    ("Delete the NDA from this matter.", None),
]


def run_suite(chain, verbose: bool) -> int:
    agree = checked = 0
    print(f"model: {MODEL}\n")
    for message, expected in SUITE:
        r = classify(chain, message)
        if expected is None:
            ok = r["intent"] == FALLBACK and r["confidence"] < 0.6
            mark = "·" if ok else "?"
        else:
            checked += 1
            ok = r["intent"] == expected
            agree += ok
            mark = "ok" if ok else "XX"
        note = ""
        if r.get("model_intent") and r["model_intent"] != r["intent"]:
            note = f"  (model said {r['model_intent']}, floored)"
        print(f"  {mark:2} {r['intent']:7} {r['confidence']:4.2f} {r['ms']:5}ms  "
              f"{message[:52]}{note}")
        if verbose:
            print(f"        {r['reasoning']}")
    print(f"\nagreed with the expected route on {agree}/{checked}")
    return 0 if agree == checked else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a message as ask, summary or draft.")
    parser.add_argument("message", nargs="*")
    parser.add_argument("--suite", action="store_true", help="run the built-in test set")
    parser.add_argument("--verbose", "-v", action="store_true", help="show reasoning")
    parser.add_argument("--show-prompt", action="store_true", help="print the prompt and exit")
    args = parser.parse_args()

    if args.show_prompt:
        print(PROMPT.read_text(encoding="utf-8"))
        return 0

    load_env()
    chain = build_chain()

    if args.suite:
        return run_suite(chain, args.verbose)
    if not args.message:
        parser.print_help()
        return 2

    r = classify(chain, " ".join(args.message))
    print(f"intent      {r['intent']}")
    print(f"confidence  {r['confidence']:.2f}")
    print(f"reasoning   {r['reasoning']}")
    print(f"source      {r['source']}")
    print(f"latency     {r['ms']}ms")
    if r.get("model_intent") and r["model_intent"] != r["intent"]:
        print(f"note        model said {r['model_intent']}, floored to {r['intent']}")
    if r.get("error"):
        print(f"error       {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
