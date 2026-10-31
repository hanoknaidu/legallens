"""The corpus the standalone harness runs against.

This exists so the three workflows can be exercised end to end with no API, no
database and no uploaded documents — which is the whole reason this package imports
nothing from the rest of the service.

It reads `tests/eval/`: six generated contracts over 25 pages, built so that each
retrieval strategy fails differently (paraphrase gaps, term-of-art collisions,
genuine absences, and an amendment that contradicts the agreement it amends). Those
modules are plain data with no imports of their own, which is why loading them here
does not drag the service in.

**Only the CLI and the tests use this.** Nothing in the workflows imports it, and
nothing should: at integration time the pages come from `documents.all_pages()` and
the passages from `chunk_metadata`.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .retrieve import PageStore
from .types import Passage

#: `backend/tests/eval`, from `backend/src/legal_lens/orchestrator/fixtures.py`.
EVAL_DIR = Path(__file__).parents[3] / "tests" / "eval"


@lru_cache(maxsize=1)
def _eval_modules() -> tuple[Any, Any]:
    """Import the corpus modules, which live outside the package.

    `distractors.py` does `from corpus import EvalDocument`, so the directory itself
    has to be on the path rather than the files being imported by location.
    """
    if not EVAL_DIR.is_dir():
        raise RuntimeError(
            f"the eval corpus is not at {EVAL_DIR}. It ships with the repository; "
            "this harness needs it to have anything to retrieve over."
        )
    path = str(EVAL_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)

    import corpus  # type: ignore[import-not-found]
    import distractors  # type: ignore[import-not-found]

    return corpus, distractors


def documents(*, with_distractors: bool = True) -> list[Any]:
    """The corpus, optionally pooled with the near-miss documents.

    The distractors are on by default and that matters: a second MSA with different
    figures, a software licence and a second NDA share the real corpus's clause
    headings while saying different things. Without them there is nothing to rank
    *wrongly*, and the first version of the retrieval eval scored 0.97 for an embedder
    with no notion of meaning for exactly that reason.
    """
    corpus, distractors_module = _eval_modules()
    docs = list(corpus.DOCUMENTS)
    if with_distractors:
        docs += list(distractors_module.DISTRACTORS)
    return docs


def questions() -> tuple[Any, ...]:
    _eval_modules()
    import questions as questions_module  # type: ignore[import-not-found]

    return questions_module.QUESTIONS


def load(*, with_distractors: bool = True) -> tuple[PageStore, list[Passage]]:
    """Build the page store and the passages to index.

    One passage per page here, because the eval scores page recall and a chunk that
    straddled a boundary would make "did it find the right page?" ambiguous. The
    index itself takes passages of any span — see `types.Passage`.
    """
    store = PageStore()
    passages: list[Passage] = []

    for document in documents(with_distractors=with_distractors):
        pages = [(number, text) for number, text in enumerate(document.pages, start=1)]
        store.add_document(document.key, document.title, pages)
        passages += [
            Passage(
                doc_id=document.key,
                title=document.title,
                page_start=number,
                page_end=number,
                text=text,
            )
            for number, text in pages
        ]

    return store, passages
