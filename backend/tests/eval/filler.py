"""Boilerplate pages, so the candidate set is smaller than the corpus.

## Why this exists

`distractors.py` fixed the eval's first flaw: with a 14-page corpus and 8 pages kept,
most of everything reached the prompt regardless of ranking, so the metric could not
discriminate. This fixes the second, which `workflows-plan.md` §9.2 names and could
not act on:

> *"candidates == corpus on this eval, so the reranker is globally re-sorting rather
> than refining a shortlist, which is not its production job. Re-measure on a corpus
> where candidates < corpus."*

With 25 pages and 25 candidates per arm, fusion returned the entire corpus. A
reranker handed the whole corpus is doing a different job from a reranker handed the
top 25 of two hundred, and the measurement that put `bge-reranker-v2-m3` at recall@1
**0.64 against 0.67 for no reranking** was taken in the wrong regime. The reranker
has been on in production ever since, on the strength of a number nobody could trust.

These pages make the corpus large enough that 25 candidates is a shortlist. They are
not padding: **a real contract is mostly general provisions**, and the question a
production reranker actually faces is whether it can tell a clause that answers the
question from forty pages of plausible, well-drafted, entirely irrelevant legal
English. That is the thing being measured here.

## The rules these pages obey

1. **No page here answers any scored question.** The clause bank deliberately omits
   every subject the question set asks about — indemnity, insurance, governing law,
   venue, payment terms, service levels, liability caps, term, renewal, termination,
   service credits, confidentiality, the definition of Services and survival.
2. **No page here mentions an `ABSENCE_TERMS` phrase.** Adding a page that mentioned
   liquidated damages or escrow would silently convert an absence question into a
   lookup, which is exactly the mistake `validate.py` was written to catch after a
   distractor introduced force majeure and data residency.
3. **Collisions are welcome.** Several clauses here use "notice", "term", "written
   consent" and "change" in senses that do not answer anything. That is the hard case
   for a ranker and the reason this corpus is worth measuring on, rather than a
   failure of the filler.
4. **Deterministic.** Assembled by index from fixed text, with no randomness, so two
   runs score the same corpus and a change in the numbers is a change in the models.

`validate.py` enforces 1 and 2 for real.
"""

from __future__ import annotations

from corpus import EvalDocument

#: General provisions, in the register of a commercial agreement. Every one of these
#: is the kind of clause that occupies the back third of a contract and answers none
#: of the questions a lawyer asks about a specific deal.
CLAUSES: tuple[tuple[str, str], ...] = (
    (
        "INTERPRETATION",
        # "for convenience" deliberately avoided: it is `q09`'s expected phrase, and
        # `validate.py` refuses any filler that contains one. The phrase here would
        # not have answered q09 — it is reference-convenience, not
        # termination-for-convenience — but a guard that has to judge intent is a
        # guard that lets the real mistake through, so the wording moves instead.
        "The headings in this Agreement are for ease of reference only and shall "
        "not affect its construction or interpretation. References to the singular "
        "include the plural and references to the plural include the singular. The "
        'words "include", "includes" and "including" shall be construed as being '
        'followed by the words "without limitation", whether or not they are. A '
        "reference to a statute or statutory provision is a reference to it as amended, "
        "extended or re-enacted from time to time. No rule of construction requiring "
        "that this Agreement be construed against the party that drafted it shall apply "
        "to its interpretation.",
    ),
    (
        "ENTIRE AGREEMENT",
        "This Agreement, together with the schedules and any documents expressly "
        "incorporated by reference, constitutes the entire agreement between the parties "
        "with respect to its subject matter and supersedes all prior and contemporaneous "
        "proposals, understandings, representations and communications, whether written "
        "or oral. Each party acknowledges that in entering into this Agreement it has not "
        "relied on, and shall have no remedy in respect of, any statement or assurance "
        "not expressly set out herein, save that nothing in this clause limits liability "
        "for fraudulent misrepresentation.",
    ),
    (
        "SEVERABILITY",
        "If any provision of this Agreement is held to be invalid, illegal or "
        "unenforceable by a court or tribunal of competent jurisdiction, that provision "
        "shall be modified to the minimum extent necessary to render it enforceable while "
        "preserving the parties' original intent, and if no such modification is "
        "possible the provision shall be severed. The remaining provisions shall continue "
        "in full force and effect, and the severance of any provision shall not affect "
        "the validity or enforceability of that provision in any other jurisdiction.",
    ),
    (
        "NO WAIVER",
        "No failure or delay by either party in exercising any right, power or remedy "
        "under this Agreement shall operate as a waiver of it, nor shall any single or "
        "partial exercise of any right, power or remedy preclude any further exercise of "
        "that or any other right, power or remedy. A waiver of any provision shall be "
        "effective only if given in writing and signed by an authorised representative "
        "of the waiving party, and shall apply only to the instance for which it was "
        "given. The rights and remedies provided in this Agreement are cumulative and "
        "not exclusive of any rights or remedies provided by law.",
    ),
    (
        "AMENDMENTS AND VARIATIONS",
        "No amendment, variation or supplement to this Agreement shall be effective "
        "unless it is in writing, references this Agreement, and is signed by an "
        "authorised representative of each party. A purported amendment contained in an "
        "exchange of correspondence, in a purchase order, in an invoice, or in any "
        "click-through or shrink-wrap terms shall be of no effect, whether or not the "
        "receiving party objects to it. Any amendment executed in accordance with this "
        "clause forms part of this Agreement from the date stated in it.",
    ),
    (
        "RELATIONSHIP OF THE PARTIES",
        "Nothing in this Agreement shall be construed as creating a partnership, joint "
        "venture, agency, fiduciary relationship or employment relationship between the "
        "parties. Each party is an independent contractor and neither has authority to "
        "bind the other, to incur any obligation on the other's behalf, or to hold itself "
        "out as having such authority. Each party is solely responsible for the "
        "management, direction and remuneration of its own personnel, and for all "
        "employment-related obligations in respect of them.",
    ),
    (
        "THIRD-PARTY RIGHTS",
        "This Agreement is made for the benefit of the parties and their permitted "
        "successors and assigns, and is not intended to confer any right or benefit on "
        "any third party. No consent of any third party is required for any amendment to, "
        "or rescission of, this Agreement. Nothing in this clause affects the rights of a "
        "permitted assignee or of any successor in title to a party.",
    ),
    (
        "FORCE MAJEURE",
        "Neither party shall be liable for any failure or delay in performing its "
        "obligations under this Agreement to the extent that the failure or delay is "
        "caused by an event beyond its reasonable control, including acts of God, flood, "
        "earthquake, fire, epidemic, war, civil disturbance, act of terrorism, labour "
        "dispute affecting a third party, or the failure of a public telecommunications "
        "or power network. The affected party shall notify the other promptly, shall use "
        "reasonable endeavours to mitigate the effect, and shall resume performance as "
        "soon as the event ceases. Obligations to make payments already due are not "
        "excused by this clause.",
    ),
    (
        "ASSIGNMENT AND CHANGE OF CONTROL",
        "Neither party may assign, novate, charge or otherwise transfer any of its rights "
        "or obligations under this Agreement without the prior written consent of the "
        "other, such consent not to be unreasonably withheld or delayed. Either party may "
        "assign this Agreement in its entirety, on written notice and without consent, to "
        "an affiliate or to a successor in connection with a merger, reorganisation or "
        "sale of all or substantially all of its assets, provided the assignee assumes all "
        "obligations in writing.",
    ),
    (
        "NOTICES",
        "Any notice given under this Agreement shall be in writing in the English "
        "language and shall be delivered by hand, by internationally recognised courier, "
        "or by prepaid registered post to the address of the receiving party set out in "
        "the signature block, or to such other address as that party may notify in "
        "accordance with this clause. A notice delivered by hand is given on delivery; a "
        "notice sent by courier is given on signature of the courier's receipt; a notice "
        "sent by registered post is given at 9.00 am on the second business day after "
        "posting. A notice given by electronic mail is not validly given under this "
        "clause unless the receiving party acknowledges it in writing.",
    ),
    (
        "COUNTERPARTS AND ELECTRONIC EXECUTION",
        "This Agreement may be executed in any number of counterparts, each of which when "
        "executed constitutes an original and all of which together constitute one and the "
        "same instrument. Transmission of an executed counterpart by electronic mail, or "
        "execution by means of an electronic signature platform, shall be as effective as "
        "delivery of an originally signed counterpart. No counterpart shall be effective "
        "until each party has executed at least one counterpart.",
    ),
    (
        "FURTHER ASSURANCES",
        "Each party shall, at its own cost and at the reasonable request of the other, "
        "promptly execute and deliver such further documents and do such further acts as "
        "may be necessary to give full effect to this Agreement and to secure to each "
        "party the full benefit of the rights conferred by it. This obligation continues "
        "for so long as either party has any outstanding obligation under this Agreement.",
    ),
    (
        "COMPLIANCE WITH LAWS",
        "Each party shall comply with all laws, regulations and mandatory codes of "
        "practice applicable to it in the performance of this Agreement, including those "
        "relating to anti-bribery and anti-corruption, trade sanctions, export control, "
        "and the prevention of modern slavery and human trafficking in its own business "
        "and supply chain. Neither party shall take any action that would cause the other "
        "to be in breach of any such law or regulation. Each party shall maintain "
        "policies and procedures reasonably designed to ensure such compliance.",
    ),
    (
        "SUBCONTRACTING",
        "A party may engage subcontractors to perform any part of its obligations under "
        "this Agreement, provided that it remains responsible to the other party for the "
        "acts and omissions of each subcontractor as if they were its own, and provided "
        "that each subcontractor is bound by obligations no less protective than those "
        "set out in this Agreement. A list of approved subcontractors shall be maintained "
        "and made available on request.",
    ),
    (
        "PUBLICITY AND USE OF MARKS",
        "Neither party shall issue any press release or public announcement relating to "
        "this Agreement, or use the name, logo or trade marks of the other party in any "
        "marketing or promotional material, without the prior written consent of that "
        "party. Consent under this clause may be given or withheld at the sole discretion "
        "of the party whose marks are concerned, and any consent given may be revoked on "
        "thirty days' written notice.",
    ),
    (
        "RECORDS AND AUDIT",
        "Each party shall keep complete and accurate books and records relating to its "
        "performance under this Agreement for a period of six years following the "
        "transaction to which they relate. On not less than twenty business days' written "
        "notice, and not more than once in any twelve-month period, a party may appoint an "
        "independent firm of accountants to inspect those records during normal business "
        "hours for the sole purpose of verifying compliance with this Agreement. The "
        "appointing party shall bear the cost of the inspection.",
    ),
    (
        "CHANGE CONTROL PROCEDURE",
        "Either party may propose a change to the scope of work by submitting a written "
        "change request describing the proposed change and its anticipated effect. The "
        "receiving party shall respond within ten business days with an assessment of the "
        "effect on timetable, resourcing and charges. No proposed change shall be "
        "implemented until a change authorisation has been signed by an authorised "
        "representative of each party, and until then the parties shall continue to "
        "perform in accordance with the existing scope of work.",
    ),
    (
        "TAXES",
        "All charges stated are exclusive of value added tax, sales tax, goods and "
        "services tax and any similar transaction taxes, which shall be added where "
        "properly chargeable. Each party is responsible for its own income, corporation "
        "and employment taxes. Where a party is required by law to withhold or deduct any "
        "amount from a payment, it shall account for the deduction to the relevant "
        "authority and shall provide the other party with evidence of the payment "
        "sufficient to support a claim for credit or relief.",
    ),
    (
        "EQUITABLE RELIEF",
        "Each party acknowledges that damages may be an inadequate remedy for a breach of "
        "this Agreement and that the non-breaching party shall be entitled to seek "
        "injunctive or other equitable relief in any court of competent jurisdiction, "
        "without the necessity of posting a bond or proving actual damages, in addition to "
        "any other remedy available to it.",
    ),
    (
        "LANGUAGE AND GOVERNING TEXT",
        "This Agreement is drawn up in the English language. If this Agreement is "
        "translated into any other language, the English language text shall prevail in "
        "the event of any conflict or inconsistency. All notices, reports and other "
        "communications delivered under this Agreement shall be in English, and any "
        "document submitted in another language shall be accompanied by a certified "
        "English translation at the cost of the submitting party.",
    ),
)

#: The agreements the filler is attributed to. Plausible neighbours in a matter, and
#: named so no title collides with a real or distractor document.
_DOCS: tuple[tuple[str, str, str], ...] = (
    (
        "facilities_msa",
        "Facilities Management Agreement — Northwind / Halcyon Estates",
        "northwind-halcyon-facilities.pdf",
    ),
    (
        "reseller",
        "Reseller Agreement — Northwind / Pellworth Distribution",
        "northwind-pellworth-reseller.pdf",
    ),
    (
        "staffing_sow",
        "Statement of Work No. 4 — Contract Staffing Services",
        "northwind-sow-4-staffing.pdf",
    ),
    (
        "data_services",
        "Data Services Agreement — Northwind / Veridian Analytics",
        "northwind-veridian-data-services.pdf",
    ),
)

#: Pages per filler document. Twenty each over four documents takes the pooled corpus
#: past a hundred pages, so 25 candidates per arm is a quarter of it rather than all
#: of it — the regime a production reranker works in.
PAGES_PER_DOC = 20


def _page(doc_title: str, page_number: int, index: int) -> str:
    """One page: two general provisions under a running header.

    Two clauses rather than one so a page is of comparable length to a real corpus
    page; a filler page that was obviously shorter than every real page would give
    the ranker a cue that has nothing to do with meaning.
    """
    first = CLAUSES[index % len(CLAUSES)]
    second = CLAUSES[(index + 7) % len(CLAUSES)]
    # Clause numbers continue upward across the document, as they would in a real one.
    number = 11 + page_number
    return (
        f"{doc_title}\nPage {page_number} — General Provisions\n\n"
        f"{number}. {first[0]}\n\n{number}.1 {first[1]}\n\n"
        f"{number + 1}. {second[0]}\n\n{number + 1}.1 {second[1]}"
    )


FILLER: tuple[EvalDocument, ...] = tuple(
    EvalDocument(
        key=key,
        title=title,
        filename=filename,
        pages=[
            _page(title, page, doc_index * PAGES_PER_DOC + page)
            for page in range(1, PAGES_PER_DOC + 1)
        ],
    )
    for doc_index, (key, title, filename) in enumerate(_DOCS)
)
