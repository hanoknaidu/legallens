"""The evaluation question set.

Each question names the document and page that genuinely answers it, so retrieval can
be scored rather than eyeballed. `kind` groups them, because the groups fail for
different reasons and a single average hides that:

- `lookup`      — the answer shares words with the question. Lexical search alone
                  handles these; they are the floor, and a regression here means
                  something is badly wrong.
- `paraphrase`  — the answer shares almost no words with the question. This is what
                  the dense arm is for, and what the hashing embedder cannot do.
- `collision`   — the question's words appear in clauses that do not answer it
                  ("term", "service", "notice" all appear in several). Dense search
                  alone conflates these; the BM25 arm plus reranking is what separates
                  them.
- `absence`     — nothing in the corpus answers it. The correct behaviour is
                  abstention (FR-9), and `pages` is empty. Scoring retrieval on these
                  is meaningless; they score the *generation* step's honesty.
- `cross_doc`   — answering correctly requires reconciling two documents, because the
                  amendment supersedes the MSA. A system that cites only the MSA is
                  confidently out of date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["lookup", "paraphrase", "collision", "absence", "cross_doc"]


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    kind: Kind
    #: `(document key, page)` pairs that genuinely answer it. Empty for `absence`.
    pages: list[tuple[str, int]] = field(default_factory=list)
    #: A phrase that must appear in a correct answer's cited text, where one exists.
    expect_phrase: str | None = None
    note: str = ""


QUESTIONS: tuple[EvalQuestion, ...] = (
    # ---------------- lookup: the floor ----------------
    EvalQuestion(
        "q01",
        "What are the indemnification obligations?",
        "lookup",
        [("msa", 7)],
        "shall defend Customer against any claim",
    ),
    EvalQuestion(
        "q02",
        "What insurance is the Supplier required to maintain?",
        "lookup",
        [("msa", 9)],
        "commercial general liability insurance",
    ),
    EvalQuestion(
        "q03",
        "What law governs this agreement and where is venue?",
        "lookup",
        [("msa", 10)],
        "laws of the State of Delaware",
    ),
    EvalQuestion(
        "q04",
        "What are the payment terms?",
        "lookup",
        [("msa", 4)],
        "within forty-five (45) days",
    ),
    EvalQuestion(
        "q05",
        "What are the service level commitments?",
        "lookup",
        [("msa", 5)],
        "99.5%",
    ),
    # ---------------- paraphrase: no shared vocabulary ----------------
    EvalQuestion(
        "q06",
        "Is there a ceiling on how much either side can be made to pay?",
        "paraphrase",
        [("msa", 8)],
        "shall not exceed",
        note="'ceiling' and 'cap' appear nowhere; the clause says 'shall not exceed'.",
    ),
    EvalQuestion(
        "q07",
        "How long does the arrangement run before it needs renewing?",
        "paraphrase",
        [("msa", 3), ("amendment", 1)],
        "Initial Term",
        note="Asks about duration without using 'term'.",
    ),
    EvalQuestion(
        "q08",
        "What happens if the customer stops paying on time?",
        "paraphrase",
        [("msa", 4)],
        "bears interest",
        note="The clause is headed 'Late Payment' and never says 'stops paying'.",
    ),
    EvalQuestion(
        "q09",
        "Can we walk away early without giving a reason?",
        "paraphrase",
        [("msa", 9), ("amendment", 2)],
        "for convenience",
        note="Colloquial phrasing of termination for convenience.",
    ),
    EvalQuestion(
        "q10",
        "What do we get back if the system is down too often?",
        "paraphrase",
        [("msa", 5)],
        "Service Credit",
    ),
    EvalQuestion(
        "q11",
        "Who owns the information each side shares during talks?",
        "paraphrase",
        [("nda", 1)],
        "Confidential Information",
        note="Should reach the NDA, not the MSA's confidentiality clause.",
    ),
    # ---------------- collision: the words mislead ----------------
    EvalQuestion(
        "q12",
        "What is the definition of Services?",
        "collision",
        [("msa", 2)],
        "means the implementation, configuration, hosting",
        note="'Services' appears on nearly every page; only page 2 defines it.",
    ),
    EvalQuestion(
        "q13",
        "How much notice is required to stop the agreement renewing?",
        "collision",
        [("msa", 3)],
        "ninety (90) days",
        note="Several clauses specify notice periods — 5, 30, 60, 90 days. Only one is "
        "about non-renewal.",
    ),
    EvalQuestion(
        "q14",
        "Which obligations continue after the agreement ends?",
        "collision",
        [("msa", 3)],
        "survive expiry or termination",
        note="'termination' pulls strongly toward Section 10, but survival is in 2.3.",
    ),
    EvalQuestion(
        "q15",
        "What are the limits on liability for the indemnities?",
        "collision",
        [("msa", 8)],
        "indemnification obligations under",
        note="Spans two clauses: the cap in 8.2 and the carve-out in 8.3 that exempts "
        "indemnities from it. Citing only 8.2 gives the opposite answer.",
    ),
    # ---------------- cross-document: the amendment supersedes ----------------
    EvalQuestion(
        "q16",
        "How long is the initial term of the agreement?",
        "cross_doc",
        [("amendment", 1), ("msa", 3)],
        "thirty-six (36) months",
        note="The MSA says 24 months; the amendment replaces it with 36. Citing only "
        "the MSA is confidently out of date.",
    ),
    EvalQuestion(
        "q17",
        "What is the current annual subscription fee?",
        "cross_doc",
        [("amendment", 1), ("msa", 4)],
        "552,000",
        note="Increased from 480,000. Same failure mode as q16.",
    ),
    EvalQuestion(
        "q18",
        "After how long can the customer terminate for convenience?",
        "cross_doc",
        [("amendment", 2), ("msa", 9)],
        "twenty-four (24) months",
        note="MSA says 12 months and 60 days' notice; the amendment makes it 24 months "
        "and 90 days.",
    ),
    # ---------------- absence: the correct answer is 'not addressed' ----------------
    # Each of these is adjacent to something the corpus *does* contain, which is what
    # makes them hard: the tempting wrong answer is to stretch a nearby clause into an
    # answer. Validated as genuinely absent across the real corpus *and* the
    # distractors by `validate.py`.
    EvalQuestion(
        "q19",
        "What are the liquidated damages for late delivery?",
        "absence",
        [],
        note="Service Credits are the nearest thing and are not liquidated damages.",
    ),
    EvalQuestion(
        "q20",
        "Is there a most-favoured-nation pricing clause?",
        "absence",
        [],
        note="Fees and rate-revision clauses are nearby but say nothing about MFN.",
    ),
    EvalQuestion(
        "q21",
        "Does the Supplier have any non-compete or exclusivity obligation?",
        "absence",
        [],
        note="The licence grant says 'non-exclusive', which is about the licence, not "
        "an exclusivity covenant. The nearest trap in the set.",
    ),
    EvalQuestion(
        "q22",
        "Is there a source code escrow arrangement?",
        "absence",
        [],
        note="A software licence is present, so escrow is a plausible expectation — and absent.",
    ),
)


def by_kind(kind: Kind) -> list[EvalQuestion]:
    return [q for q in QUESTIONS if q.kind == kind]


def expected_pages(question: EvalQuestion) -> set[tuple[str, int]]:
    return set(question.pages)
