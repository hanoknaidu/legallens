"""Near-miss documents, so retrieval has something to get wrong.

Without these the eval is not measuring anything: the real corpus is 14 pages, the
pipeline keeps 8, and so 57% of everything reaches the prompt regardless of how the
ranking works. Page recall@8 came out at 0.97 for a hashed bag of words with no
notion of meaning — not because that embedder is good, but because the task was too
easy for the metric to discriminate.

What makes retrieval hard in real use is not finding a clause in a short document. It
is picking **which** of a matter's many contracts holds the clause you asked about,
when they all share the same boilerplate, the same clause headings, and different
numbers. These documents supply exactly that: the same clause types as the real
corpus, with different parties, different figures, and in several places the opposite
substance — a mutual indemnity where the real one is one-way, an arbitration clause
where the real corpus deliberately has none.

A system that cites a distractor is confidently wrong in the way that matters most:
the quoted text is real, the page is right, and the document is the wrong contract.
"""

from __future__ import annotations

from corpus import EvalDocument

# A second MSA, same structure, different counterparty and figures. The most
# confusable document in the set.
VENDOR_MSA = [
    "MASTER SERVICES AGREEMENT\n\n"
    "This Master Services Agreement is entered into as of 8 January 2026 between "
    'Northwind Logistics, Inc. ("Customer") and Calder Freight Systems, Inc., an Ohio '
    'corporation ("Supplier").\n\n'
    "1. DEFINITIONS\n\n"
    '1.1 "Services" means the freight brokerage and load-matching services described '
    "in the Service Schedule attached to this Agreement.\n\n"
    '1.2 "Confidential Information" means any non-public information disclosed by '
    "either party that is marked confidential at the time of disclosure.",
    "2. TERM AND RENEWAL\n\n"
    "2.1 Initial Term. This Agreement commences on the Effective Date and continues "
    'for a period of twelve (12) months thereafter (the "Initial Term").\n\n'
    "2.2 Renewal. This Agreement renews automatically for successive periods of six "
    "(6) months unless either party gives written notice of non-renewal not less than "
    "forty-five (45) days before the end of the then-current term.\n\n"
    "3. FEES AND PAYMENT\n\n"
    "3.1 Fees. Customer shall pay Supplier a monthly service fee of USD 62,500, "
    "payable in arrears.\n\n"
    "3.2 Invoicing. Each invoice is due and payable within thirty (30) days of the "
    "invoice date.\n\n"
    "3.3 Late Payment. Any amount not paid when due bears interest at the rate of one "
    "and one-half percent (1.5%) per month from the due date until paid.",
    "4. INDEMNIFICATION\n\n"
    "4.1 Mutual Indemnity. Each party shall defend and indemnify the other against "
    "any claim brought by a third party arising out of the indemnifying party's "
    "negligence, wilful misconduct, or breach of this Agreement, and shall indemnify "
    "the other against damages and costs finally awarded or agreed in settlement in "
    "respect of such claim.\n\n"
    "4.2 Conditions. The indemnified party shall notify the indemnifying party in "
    "writing within fifteen (15) days of becoming aware of any claim for which "
    "indemnity is sought and shall give the indemnifying party sole control of the "
    "defence.",
    "5. LIMITATION OF LIABILITY\n\n"
    "5.1 Exclusion. Neither party is liable for any indirect, incidental or "
    "consequential loss, including loss of profit or loss of business opportunity.\n\n"
    "5.2 Aggregate Cap. The total aggregate liability of each party arising out of or "
    "in connection with this Agreement shall not exceed the total fees paid by "
    "Customer in the six (6) months immediately preceding the event giving rise to the "
    "liability.\n\n"
    "6. INSURANCE\n\n"
    "6.1 Supplier shall maintain commercial general liability insurance with limits of "
    "not less than USD 2,000,000 per occurrence and motor carrier liability insurance "
    "with limits of not less than USD 1,000,000 per occurrence.",
    "7. TERMINATION\n\n"
    "7.1 For Convenience. Either party may terminate this Agreement for convenience "
    "at any time by giving the other not less than thirty (30) days' written notice.\n\n"
    "7.2 For Cause. Either party may terminate immediately on written notice if the "
    "other commits a material breach and fails to remedy it within fifteen (15) days "
    "of written notice.\n\n"
    "8. DISPUTE RESOLUTION\n\n"
    "8.1 Arbitration. Any dispute arising out of or in connection with this Agreement "
    "shall be finally resolved by binding arbitration administered in Columbus, Ohio "
    "under the Commercial Arbitration Rules of the American Arbitration Association, "
    "and judgment on the award may be entered in any court of competent "
    "jurisdiction.\n\n"
    "9. GOVERNING LAW\n\n"
    "9.1 This Agreement is governed by the laws of the State of Ohio.",
]

# A software licence, which shares vocabulary with the MSA but is a different
# instrument — and carries the force-majeure and assignment clauses the real corpus
# deliberately lacks.
SOFTWARE_LICENCE = [
    "SOFTWARE LICENCE AGREEMENT\n\n"
    "This Software Licence Agreement is made as of 19 November 2025 between Meridian "
    'Analytics Ltd ("Licensor") and Northwind Logistics, Inc. ("Licensee").\n\n'
    "1. LICENCE GRANT\n\n"
    "1.1 Licensor grants Licensee a non-exclusive, non-transferable licence to use the "
    "Software for its internal business purposes for the term of this Agreement.\n\n"
    "1.2 Licensee shall not sublicense, rent, lease or otherwise make the Software "
    "available to any third party.",
    "2. TERM\n\n"
    "2.1 This Agreement continues for a period of sixty (60) months from the "
    "Effective Date unless terminated earlier in accordance with Section 6.\n\n"
    "3. FEES\n\n"
    "3.1 Licensee shall pay an annual licence fee of USD 215,000, payable annually in "
    "advance within sixty (60) days of invoice.\n\n"
    "4. ASSIGNMENT\n\n"
    "4.1 Neither party may assign or transfer this Agreement, in whole or in part, "
    "without the prior written consent of the other party, except that either party "
    "may assign this Agreement without consent to a successor in connection with a "
    "merger, acquisition or sale of substantially all of its assets.",
    "5. FORCE MAJEURE\n\n"
    "5.1 Neither party is liable for any failure or delay in performing its "
    "obligations under this Agreement to the extent that the failure or delay is "
    "caused by an event beyond its reasonable control, including act of God, flood, "
    "fire, earthquake, epidemic, war, terrorism, labour dispute, or failure of a "
    "public utility or telecommunications network.\n\n"
    "5.2 The affected party shall notify the other promptly and shall use reasonable "
    "endeavours to resume performance. If the event continues for more than sixty "
    "(60) consecutive days, either party may terminate this Agreement on written "
    "notice.\n\n"
    "6. TERMINATION\n\n"
    "6.1 Either party may terminate this Agreement on written notice if the other "
    "commits a material breach that remains unremedied for thirty (30) days.",
    "7. DATA PROTECTION AND RESIDENCY\n\n"
    "7.1 Licensor shall process Licensee data only in data centres located within the "
    "United States and shall not transfer any Licensee data outside the United States "
    "without Licensee's prior written consent.\n\n"
    "7.2 Licensor shall maintain information security controls consistent with ISO/IEC "
    "27001 and shall permit Licensee to audit those controls not more than once in any "
    "twelve-month period.\n\n"
    "8. GOVERNING LAW\n\n"
    "8.1 This Agreement is governed by the laws of the State of New York, and the "
    "parties submit to the exclusive jurisdiction of the federal and state courts "
    "located in New York County.",
]

# A second NDA, so NDA questions have a competitor too.
CONSULTING_NDA = [
    "CONFIDENTIALITY AGREEMENT\n\n"
    "This Confidentiality Agreement is made as of 5 June 2026 between Northwind "
    "Logistics, Inc. and Pike & Rowe Advisory LLP.\n\n"
    "1. CONFIDENTIAL INFORMATION\n\n"
    '1.1 "Confidential Information" means any information relating to the business, '
    "operations, finances or strategy of the disclosing party, in any form.\n\n"
    "2. OBLIGATIONS\n\n"
    "2.1 The receiving party shall keep the Confidential Information secret and shall "
    "not use it for any purpose other than the provision of advisory services to the "
    "disclosing party.\n\n"
    "2.2 The obligations under this Agreement continue for a period of five (5) years "
    "from the date of this Agreement.",
    "3. PERMITTED DISCLOSURE\n\n"
    "3.1 The receiving party may disclose Confidential Information to the extent "
    "required by law, by a court of competent jurisdiction or by a regulatory "
    "authority, provided that it gives the disclosing party as much notice as is "
    "reasonably practicable.\n\n"
    "4. RETURN\n\n"
    "4.1 The receiving party shall return or destroy all Confidential Information "
    "within thirty (30) days of written request.\n\n"
    "5. GOVERNING LAW\n\n"
    "5.1 This Agreement is governed by the laws of England and Wales.",
]

DISTRACTORS: tuple[EvalDocument, ...] = (
    EvalDocument(
        key="vendor_msa",
        title="Master Services Agreement — Northwind / Calder Freight Systems",
        filename="northwind-calder-msa.pdf",
        pages=VENDOR_MSA,
    ),
    EvalDocument(
        key="licence",
        title="Software Licence Agreement — Meridian Analytics / Northwind",
        filename="meridian-northwind-licence.pdf",
        pages=SOFTWARE_LICENCE,
    ),
    EvalDocument(
        key="consulting_nda",
        title="Confidentiality Agreement — Northwind / Pike & Rowe Advisory",
        filename="northwind-pike-rowe-nda.pdf",
        pages=CONSULTING_NDA,
    ),
)
