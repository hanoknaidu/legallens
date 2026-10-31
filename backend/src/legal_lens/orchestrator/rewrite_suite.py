"""The rewrite step's test set.

The retrieval eval (`tests/eval/`) has no multi-turn questions, so it cannot score
this step at all. These cases exist to make a prompt edit measurable: run
`--rewrite-suite` before and after and see which guardrail moved.

Each case fixes only the **verdict** — rewrite or decline — and, where a rewrite is
correct, a few terms the result must or must not contain. Pinning the exact wording
would make the suite fail on a paraphrase that is perfectly good, which trains you to
ignore it.

The cases are weighted towards declining, because that is the asymmetry that matters:
a missed rewrite costs a slightly worse search, while a wrong rewrite retrieves the
wrong clauses and produces a confident answer to a question nobody asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .types import Turn

Verdict = Literal["rewrite", "no_rewrite"]


@dataclass(frozen=True)
class RewriteCase:
    id: str
    history: list[Turn]
    question: str
    expected: Verdict
    #: Terms a correct rewrite must contain, lower-cased.
    must_contain: list[str] = field(default_factory=list)
    #: Terms a correct rewrite must NOT contain — the invented-detail failures.
    must_not_contain: list[str] = field(default_factory=list)
    why: str = ""


def u(content: str) -> Turn:
    return Turn(role="user", content=content)


def a(content: str) -> Turn:
    return Turn(role="assistant", content=content)


CASES: tuple[RewriteCase, ...] = (
    # ---------------- should rewrite: the referent is unambiguous ----------------
    RewriteCase(
        "r01",
        [
            u("What is the indemnification cap in the Arbor MSA?"),
            a("Section 8.2 caps aggregate liability at the fees paid in the prior twelve months."),
        ],
        "Does it have any carve-outs?",
        "rewrite",
        must_contain=["carve", "cap"],
        why="'it' resolves to the indemnification cap, and nothing else is in play.",
    ),
    RewriteCase(
        "r02",
        [
            u("Summarise the Mutual NDA."),
            a("It runs for three years and covers information marked confidential."),
        ],
        "What about the survival period?",
        "rewrite",
        must_contain=["nda"],
        must_not_contain=["three years"],
        why="The subject carries over; the assistant's own answer must not be folded in.",
    ),
    RewriteCase(
        "r03",
        [
            u("Who are the parties to the Master Services Agreement?"),
            a("Northwind Logistics, Inc. and Arbor Systems LLC."),
        ],
        "Which one has to carry insurance?",
        "rewrite",
        must_contain=["insurance"],
        why="'which one' resolves to the two named parties.",
    ),
    RewriteCase(
        "r04",
        [
            u("Does Amendment No. 2 change the annual fee?"),
            a("Yes — it raises it from $420,000 to $465,000 effective 1 January."),
        ],
        "And the term?",
        "rewrite",
        must_contain=["amendment", "term"],
        must_not_contain=["465", "420", "fee"],
        why="Elliptical follow-up; the subject carries, the answered figure does not.",
    ),
    # ---------------- should decline: already self-contained ----------------
    RewriteCase(
        "r05",
        [
            u("What is the governing law of the MSA?"),
            a("Delaware, with venue in New Castle County."),
        ],
        "What notice period applies to termination for convenience under the MSA?",
        "no_rewrite",
        why="Names its own subject; the history adds nothing it is missing.",
    ),
    RewriteCase(
        "r06",
        [
            u("Summarise the NDA."),
            a("Three-year term, mutual obligations, no assignment clause."),
        ],
        "Is there an arbitration clause anywhere in these documents?",
        "no_rewrite",
        why="Complete on its own, and explicitly scoped wider than the history.",
    ),
    # ---------------- should decline: a different subject ----------------
    RewriteCase(
        "r07",
        [
            u("What are the payment terms?"),
            a("Net 30 from receipt of a correct invoice."),
        ],
        "What does the NDA say about return of materials?",
        "no_rewrite",
        why="New subject and new document. Dragging payment terms in would narrow it.",
    ),
    RewriteCase(
        "r08",
        [
            u("Explain the liability carve-outs in clause 8.3."),
            a("Carve-outs for confidentiality breach, indemnity and wilful misconduct."),
        ],
        "Who signed the amendment?",
        "no_rewrite",
        why="Mutually exclusive topics; the history resolves nothing here.",
    ),
    # ---------------- should decline: no referent in the history ----------------
    RewriteCase(
        "r09",
        [
            u("What is the term of the MSA?"),
            a("An initial term of three years from the Effective Date."),
        ],
        "What about the second one?",
        "no_rewrite",
        why="Nothing in the history enumerates anything, so 'the second one' has no referent.",
    ),
    RewriteCase(
        "r10",
        [
            u("Hello"),
            a("Ask me about the documents in this matter."),
        ],
        "What does that mean for us?",
        "no_rewrite",
        why="No substantive history to resolve against.",
    ),
    # ---------------- should decline: ambiguous between two candidates ----------------
    RewriteCase(
        "r11",
        [
            u("Compare the termination clause in the MSA with the one in the NDA."),
            a(
                "The MSA allows termination for convenience on 60 days' notice; the NDA "
                "terminates only on written agreement."
            ),
        ],
        "Is that clause unusual?",
        "no_rewrite",
        why="'that clause' could be either of two clauses just discussed. Guessing is "
        "the invisible failure the prompt exists to prevent.",
    ),
    RewriteCase(
        "r12",
        [
            u("What are the notice provisions?"),
            a(
                "Clause 12.1 requires written notice by courier; clause 4.3 requires "
                "30 days' notice of a service change."
            ),
        ],
        "Can that be done by email?",
        "no_rewrite",
        why="Two notice provisions are live; 'that' picks neither.",
    ),
    # ---------------- should decline: scope creep is the tempting error ----------------
    RewriteCase(
        "r13",
        [
            u("Summarise the indemnification clause of the MSA."),
            a(
                "The Supplier defends and indemnifies the Customer against third-party "
                "IP claims, capped at twelve months' fees."
            ),
        ],
        "What about indemnification in the NDA?",
        "no_rewrite",
        why="Self-contained: it names both the subject and the document.",
    ),
    RewriteCase(
        "r14",
        [
            u("Does the MSA cap liability?"),
            a("Yes, at the fees paid in the preceding twelve months."),
        ],
        "What about indemnification?",
        "rewrite",
        must_contain=["indemnification", "msa"],
        # The classic over-reach: inheriting "cap" from the previous answer turns a
        # broad question into a narrow one, and the attorney cannot see it happened.
        must_not_contain=["cap", "twelve months", "limit"],
        why="Resolve the document, not the previous answer's subject matter.",
    ),
    # ---------------- should decline: quoted language must survive ----------------
    RewriteCase(
        "r15",
        [
            u("What does clause 7 say?"),
            a("It requires the Supplier to maintain commercial general liability insurance."),
        ],
        'Where else does the phrase "commercially reasonable efforts" appear?',
        "no_rewrite",
        why="Self-contained, and carries a quoted phrase that must not be reworded.",
    ),
)
