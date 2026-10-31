"""Check the eval set is internally consistent before anything is measured against it.

An eval with a wrong answer key is worse than no eval: it reports confident numbers
for the wrong thing. This caught two real mistakes already — a distractor document
that introduced force-majeure, assignment and data-residency clauses, which silently
invalidated four `absence` questions that had been correct before it was added.

Run: `uv run python tests/eval/validate.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from corpus import DOCUMENTS  # noqa: E402
from distractors import DISTRACTORS  # noqa: E402
from filler import FILLER  # noqa: E402
from questions import QUESTIONS  # noqa: E402

#: Terms that must appear nowhere in the pooled corpus for an absence question to be
#: honest. Checked case-insensitively against every page of every document.
ABSENCE_TERMS: dict[str, list[str]] = {
    "q19": ["liquidated damage"],
    "q20": ["most-favoured", "most favoured", "most-favored", "most favored"],
    "q21": ["non-compete", "noncompete", "exclusivity", "exclusive dealing"],
    "q22": ["escrow"],
}


def main() -> int:
    real_pages = {(d.key, i + 1): p for d in DOCUMENTS for i, p in enumerate(d.pages)}
    all_docs = (*DOCUMENTS, *DISTRACTORS, *FILLER)
    all_pages = {(d.key, i + 1): p for d in all_docs for i, p in enumerate(d.pages)}
    pooled = "\n".join(all_pages.values()).lower()
    filler_text = "\n".join(p for d in FILLER for p in d.pages).lower()

    problems: list[str] = []

    # The boilerplate exists to enlarge the haystack, and it is only honest if it
    # answers nothing. Two automated guards, because "I was careful when I wrote it"
    # is exactly the assurance that failed when a distractor introduced force
    # majeure and quietly invalidated four absence questions.
    #
    # Guard one: no scored question's expected phrase may appear in the filler. If it
    # does, a filler page is a correct answer that the answer key does not name, and
    # every recall figure is understated.
    for q in QUESTIONS:
        if q.kind == "absence" or not q.expect_phrase:
            continue
        if q.expect_phrase.lower() in filler_text:
            problems.append(
                f"{q.id}: expected phrase {q.expect_phrase!r} appears in the filler, "
                "so a boilerplate page answers it"
            )

    # Guard two: the filler must not name a subject the question set asks about. A
    # weaker check than reading it, and it catches the whole-clause mistake — adding
    # an indemnity or a liability cap to the boilerplate.
    for subject in (
        "indemnif",
        "insurance",
        "service level",
        "service credit",
        "aggregate liability",
        "limitation of liability",
        "governing law",
        "venue lies",
        "payment terms",
        "net thirty",
        "initial term",
        "renewal term",
        "terminate for convenience",
        "survive termination",
        "confidential information",
    ):
        if subject in filler_text:
            problems.append(
                f"filler mentions {subject!r} — it is meant to answer nothing, and a "
                "page that discusses this may be a correct answer the key omits"
            )

    keys = [d.key for d in all_docs]
    if len(keys) != len(set(keys)):
        problems.append(f"duplicate document keys: {keys}")

    for q in QUESTIONS:
        if q.kind == "absence":
            if q.pages:
                problems.append(f"{q.id}: an absence question must name no pages")
            for term in ABSENCE_TERMS.get(q.id, []):
                if term in pooled:
                    problems.append(f"{q.id}: corpus mentions {term!r} — not a genuine absence")
            continue

        if not q.pages:
            problems.append(f"{q.id}: a scored question must name at least one page")
        for ref in q.pages:
            if ref not in real_pages:
                problems.append(f"{q.id}: names {ref}, which does not exist")
            elif ref not in real_pages:
                problems.append(f"{q.id}: names a distractor page as a correct answer")

        if q.expect_phrase:
            if not any(q.expect_phrase in real_pages[r] for r in q.pages if r in real_pages):
                where = ", ".join(f"{k} p{p}" for k, p in q.pages)
                problems.append(f"{q.id}: phrase {q.expect_phrase!r} is not on any of [{where}]")

    filler_pages = sum(len(d.pages) for d in FILLER)
    print(
        f"documents: {len(DOCUMENTS)} real + {len(DISTRACTORS)} distractor + {len(FILLER)} filler"
    )
    print(
        f"pages:     {len(real_pages)} real + "
        f"{len(all_pages) - len(real_pages) - filler_pages} distractor + "
        f"{filler_pages} filler = {len(all_pages)}"
    )
    print(f"questions: {len(QUESTIONS)}")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for problem in problems:
            print("  -", problem)
        return 1
    print(
        "\nconsistent: every answer key exists, every phrase is on its page, "
        "every absence is genuine"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
