"""The orchestration layer.

Deliberately **independent**: nothing in this package imports from the rest of
`legal_lens`, and nothing in the rest of `legal_lens` imports from here. It can be
run, tested and changed without the API, the database or the frontend, and it carries
its own configuration in `config.py`. Wiring it into `POST /chat/message` is a later
step.

It does two things:

- **Routes** a message to `ask`, `summary` or `draft` (`intent.py`).
- **Runs** each of those three workflows end to end (`workflows/`), over the hybrid
  retrieval pipeline they share (`retrieve/`).

`cli.py` drives all of it from a terminal, against the generated corpus in
`tests/eval/`, so a prompt can be changed and measured without booting anything.

The heavy dependencies are imported inside the constructors that need them, so this
module imports cleanly whether or not the `workflows` extra is installed.
"""

from .config import OrchestratorConfig
from .intent import (
    DEFAULT_MODEL,
    IntentClassifier,
    IntentResult,
    load_prompt,
)
from .types import Citation, Page, Passage, Retrieved, Turn, Usage

__all__ = [
    "DEFAULT_MODEL",
    "Citation",
    "IntentClassifier",
    "IntentResult",
    "OrchestratorConfig",
    "Page",
    "Passage",
    "Retrieved",
    "Turn",
    "Usage",
    "load_prompt",
]
