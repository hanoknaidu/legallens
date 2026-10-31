#!/usr/bin/env python3
"""Try the intent router on one message.

A thin wrapper so a file-path invocation works:

    uv run python scripts/try_intent_router.py "Draft me a memo for legal contract"
    uv run python scripts/try_intent_router.py --suite --verbose

The classifier itself lives in `legal_lens/orchestrator/`, which is a package rather
than a script, so its own entry point is a module:

    uv run python -m legal_lens.orchestrator.cli "..."

Both reach the same code. This exists only so the path form works too.
"""

from __future__ import annotations

import pathlib
import sys

# Let the script run from anywhere without the package being installed first.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    try:
        from legal_lens.orchestrator.cli import main as cli_main
    except ImportError as exc:
        if "langchain" in str(exc).lower():
            # LangChain is an optional extra so the API boots without it.
            print(
                "error: the orchestrator needs LangChain, which is an optional extra.\n"
                "  Install it once:   uv sync --extra orchestrator\n"
                "  Or run with it:    uv run --extra orchestrator python "
                'scripts/try_intent_router.py "your message"',
                file=sys.stderr,
            )
            return 2
        raise
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
