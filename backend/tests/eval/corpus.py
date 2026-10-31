"""A representative corpus for retrieval evaluation.

Generated rather than taken from real contracts, which keeps privileged text out of
the repository. The wording follows the registers these documents actually use —
numbered clauses, defined terms introduced with `means`, cross-references, and the
stilted constructions ("Notwithstanding the foregoing") that make legal prose hard to
retrieve over.

Four properties are deliberate, because each one breaks a different retrieval
strategy:

1. **Paraphrase gaps.** Several answers use none of the words a user would ask with —
   "cap on liability" appears as "shall not exceed", "how long does it run" as
   "Initial Term". Lexical search alone misses these; this is what the dense arm is
   for.
2. **Term-of-art collisions.** "Term" means duration in one clause and a contractual
   provision in another; "Service" is a defined term *and* an ordinary word. Dense
   search alone conflates these; this is what the BM25 arm is for.
3. **Genuine absences.** There is no force-majeure clause, no assignment clause, and
   no arbitration clause anywhere in the corpus. A system that answers questions
   about them is hallucinating, and a system that retrieves fragments cannot prove
   they are missing (FR-13, BRD §4.2).
4. **Cross-document contradiction.** The amendment changes the MSA's term and fee.
   Answering "how long does the agreement run" correctly requires reconciling two
   documents — the case that decides the summary model routing.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Documents. One list entry per page, so a citation's page is checkable.
# ---------------------------------------------------------------------------

MSA_PAGES: list[str] = [
    # page 1
    "MASTER SERVICES AGREEMENT\n\n"
    'This Master Services Agreement (this "Agreement") is entered into as of 14 '
    'March 2026 (the "Effective Date") by and between Northwind Logistics, Inc., a '
    "Delaware corporation with its principal place of business at 1100 Harbor Way, "
    'Wilmington, Delaware ("Customer"), and Arbor Systems LLC, a New York limited '
    "liability company with its principal place of business at 48 Chandler Street, "
    'Rochester, New York ("Supplier").\n\n'
    "RECITALS\n\n"
    "WHEREAS, Customer operates regional distribution facilities and requires "
    "warehouse management software and related professional services; and\n\n"
    "WHEREAS, Supplier is engaged in the business of providing such software and "
    "services;\n\n"
    "NOW, THEREFORE, in consideration of the mutual covenants contained herein, the "
    "parties agree as follows.",
    # page 2
    "1. DEFINITIONS\n\n"
    '1.1 "Services" means the implementation, configuration, hosting and support '
    "services described in one or more Statements of Work executed by the parties "
    "under this Agreement.\n\n"
    '1.2 "Deliverables" means any report, configuration, interface or other work '
    "product that Supplier is required to deliver under a Statement of Work.\n\n"
    '1.3 "Confidential Information" means any non-public information disclosed by '
    "either party to the other, whether orally, in writing or by inspection of "
    "tangible objects, that is designated as confidential or that a reasonable person "
    "would understand to be confidential given its nature and the circumstances of "
    "disclosure.\n\n"
    '1.4 "Customer Data" means all data, records and content that Customer or its '
    "authorised users submit to or generate within the Services.\n\n"
    '1.5 "Service Credit" means the credit calculated under Section 4.3 and applied '
    "against fees otherwise payable.",
    # page 3
    "2. TERM AND RENEWAL\n\n"
    "2.1 Initial Term. This Agreement commences on the Effective Date and continues "
    'for a period of twenty-four (24) months thereafter (the "Initial Term"), unless '
    "earlier terminated in accordance with Section 10.\n\n"
    "2.2 Renewal. Following the Initial Term, this Agreement renews automatically for "
    'successive periods of twelve (12) months each (each, a "Renewal Term"), unless '
    "either party gives written notice of non-renewal not less than ninety (90) days "
    "before the end of the then-current term.\n\n"
    "2.3 Effect of Expiry. Expiry of this Agreement does not relieve either party of "
    "any obligation accrued before the date of expiry, and the provisions of Sections "
    "1, 6, 7, 8 and 11 survive expiry or termination.",
    # page 4
    "3. FEES AND PAYMENT\n\n"
    "3.1 Fees. Customer shall pay Supplier an annual subscription fee of USD "
    "480,000, payable in equal quarterly instalments in advance.\n\n"
    "3.2 Professional Services. Services performed outside the scope of a Statement "
    "of Work are chargeable at Supplier's then-current time-and-materials rates, "
    "which Supplier may revise not more than once in any twelve-month period.\n\n"
    "3.3 Invoicing. Supplier shall invoice Customer quarterly in advance. Each invoice "
    "is due and payable within forty-five (45) days of the invoice date.\n\n"
    "3.4 Late Payment. Any amount not paid when due bears interest at the rate of one "
    "percent (1%) per month, or the maximum rate permitted by applicable law, "
    "whichever is lower, from the due date until paid in full.\n\n"
    "3.5 Disputed Amounts. Customer may withhold payment of any amount it disputes in "
    "good faith, provided that Customer pays all undisputed amounts when due and "
    "notifies Supplier of the basis for the dispute within thirty (30) days of the "
    "invoice date.",
    # page 5
    "4. SERVICE LEVELS\n\n"
    "4.1 Availability. Supplier shall make the Services available not less than "
    "99.5% of the time in each calendar month, excluding scheduled maintenance of "
    "which Supplier has given Customer at least five (5) business days' notice.\n\n"
    "4.2 Support Response. Supplier shall acknowledge any Severity 1 incident within "
    "one (1) hour and shall provide a resolution or documented workaround within eight "
    "(8) hours of acknowledgement.\n\n"
    "4.3 Service Credits. If availability in any calendar month falls below 99.5%, "
    "Customer is entitled to a Service Credit of five percent (5%) of the monthly "
    "portion of the annual subscription fee for each full percentage point below the "
    "committed level, up to a maximum of thirty percent (30%) of that monthly "
    "portion. Service Credits are Customer's sole financial remedy for failure to meet "
    "the availability commitment.",
    # page 6
    "5. CUSTOMER OBLIGATIONS\n\n"
    "5.1 Cooperation. Customer shall provide Supplier with such access, information "
    "and personnel as Supplier reasonably requires in order to perform the Services.\n\n"
    "5.2 Authorised Users. Customer is responsible for all activity conducted under "
    "the credentials of its authorised users and shall notify Supplier promptly of any "
    "credential it believes to have been compromised.\n\n"
    "5.3 Acceptable Use. Customer shall not use the Services to store or transmit any "
    "material that infringes the intellectual property rights of a third party.\n\n"
    "6. CONFIDENTIALITY\n\n"
    "6.1 Obligation. Each party shall hold the other's Confidential Information in "
    "confidence and shall not disclose it to any third party except to those of its "
    "employees, advisers and contractors who need to know it for the purposes of this "
    "Agreement and who are bound by obligations of confidentiality no less protective "
    "than those set out here.\n\n"
    "6.2 Exclusions. The obligations in Section 6.1 do not apply to information that "
    "is or becomes publicly available through no act of the receiving party, that the "
    "receiving party already lawfully possessed without restriction, or that the "
    "receiving party independently develops without reference to the disclosing "
    "party's Confidential Information.",
    # page 7
    "7. INDEMNIFICATION\n\n"
    "7.1 By Supplier. Supplier shall defend Customer against any claim brought by a "
    "third party alleging that the Services, as provided by Supplier and used in "
    "accordance with this Agreement, infringe any patent, copyright or trade secret of "
    "that third party, and shall indemnify Customer against damages and costs finally "
    "awarded against Customer or agreed in settlement in respect of such claim.\n\n"
    "7.2 By Customer. Customer shall defend Supplier against any claim brought by a "
    "third party arising out of Customer Data or out of Customer's use of the Services "
    "in breach of Section 5.3, and shall indemnify Supplier against damages and costs "
    "finally awarded or agreed in settlement in respect of such claim.\n\n"
    "7.3 Conditions. The indemnified party shall notify the indemnifying party "
    "promptly in writing of any claim for which indemnity is sought, shall give the "
    "indemnifying party sole control of the defence and settlement of that claim, and "
    "shall provide reasonable cooperation at the indemnifying party's expense. Failure "
    "to notify promptly relieves the indemnifying party of its obligations only to the "
    "extent it is materially prejudiced by the delay.\n\n"
    "7.4 Exclusive Remedy. Section 7.1 states Supplier's entire liability, and "
    "Customer's exclusive remedy, in respect of any claim of infringement.",
    # page 8
    "8. LIMITATION OF LIABILITY\n\n"
    "8.1 Exclusion of Indirect Loss. Neither party is liable to the other for any "
    "loss of profit, loss of revenue, loss of anticipated savings, loss of or "
    "corruption of data, or any indirect or consequential loss, in each case however "
    "arising and whether or not the possibility of such loss was foreseeable.\n\n"
    "8.2 Aggregate Cap. Subject to Section 8.3, the total aggregate liability of each "
    "party arising out of or in connection with this Agreement, whether in contract, "
    "tort (including negligence) or otherwise, shall not exceed an amount equal to the "
    "fees paid and payable by Customer under this Agreement in the twelve (12) months "
    "immediately preceding the event giving rise to the liability.\n\n"
    "8.3 Carve-outs. Nothing in this Agreement limits or excludes either party's "
    "liability for death or personal injury caused by its negligence, for fraud or "
    "fraudulent misrepresentation, for a party's indemnification obligations under "
    "Section 7, or for any liability that cannot lawfully be limited.\n\n"
    "8.4 Allocation of Risk. The parties acknowledge that the limitations in this "
    "Section 8 reflect an agreed allocation of risk and are reflected in the fees.",
    # page 9
    "9. INSURANCE\n\n"
    "9.1 Required Coverage. Supplier shall maintain, at its own expense and "
    "throughout the term of this Agreement, commercial general liability insurance "
    "with limits of not less than USD 5,000,000 per occurrence and USD 10,000,000 in "
    "the aggregate, together with professional liability (errors and omissions) "
    "insurance with limits of not less than USD 5,000,000 per claim.\n\n"
    "9.2 Evidence. Supplier shall provide Customer with certificates of insurance "
    "evidencing the coverage required by Section 9.1 within ten (10) business days of "
    "Customer's written request, and shall give Customer not less than thirty (30) "
    "days' written notice of any cancellation or material reduction in that coverage.\n\n"
    "10. TERMINATION\n\n"
    "10.1 For Convenience. Customer may terminate this Agreement for convenience at "
    "any time after the first twelve (12) months of the Initial Term by giving "
    "Supplier not less than sixty (60) days' written notice.\n\n"
    "10.2 For Cause. Either party may terminate this Agreement immediately on written "
    "notice if the other party commits a material breach of this Agreement and fails "
    "to remedy that breach within thirty (30) days after receiving written notice "
    "requiring it to do so.",
    # page 10
    "10.3 Insolvency. Either party may terminate this Agreement immediately on "
    "written notice if the other party becomes insolvent, enters into liquidation or "
    "administration, or makes any arrangement with its creditors generally.\n\n"
    "10.4 Consequences. On termination or expiry, Customer shall pay all fees accrued "
    "up to the effective date of termination, and Supplier shall, at Customer's "
    "written request made within thirty (30) days, return Customer Data in a "
    "structured, machine-readable format and thereafter delete all remaining copies.\n\n"
    "11. GOVERNING LAW AND VENUE\n\n"
    "11.1 Governing Law. This Agreement and any dispute arising out of or in "
    "connection with it are governed by the laws of the State of Delaware, without "
    "regard to its conflict of laws principles.\n\n"
    "11.2 Venue. The parties submit to the exclusive jurisdiction of the state and "
    "federal courts located in New Castle County, Delaware, and each party waives any "
    "objection to venue in those courts on grounds of inconvenient forum.\n\n"
    "11.3 Entire Agreement. This Agreement, together with any Statement of Work "
    "executed under it, constitutes the entire agreement between the parties in "
    "respect of its subject matter and supersedes all prior proposals and "
    "understandings, whether written or oral.",
]

AMENDMENT_PAGES: list[str] = [
    "AMENDMENT NO. 2 TO MASTER SERVICES AGREEMENT\n\n"
    'This Amendment No. 2 (this "Amendment") is made as of 1 September 2026 between '
    'Northwind Logistics, Inc. ("Customer") and Arbor Systems LLC ("Supplier"), and '
    "amends the Master Services Agreement between them dated 14 March 2026 (the "
    '"Agreement").\n\n'
    "Capitalised terms used but not defined in this Amendment have the meanings given "
    "to them in the Agreement.\n\n"
    "1. AMENDMENT TO TERM\n\n"
    "1.1 Section 2.1 of the Agreement is deleted and replaced with the following: "
    '"This Agreement commences on the Effective Date and continues for a period of '
    "thirty-six (36) months thereafter (the 'Initial Term'), unless earlier terminated "
    'in accordance with Section 10."\n\n'
    "2. AMENDMENT TO FEES\n\n"
    "2.1 With effect from 1 October 2026, the annual subscription fee stated in "
    "Section 3.1 of the Agreement is increased from USD 480,000 to USD 552,000, "
    "payable in equal quarterly instalments in advance.\n\n"
    "2.2 No other change is made to Section 3 of the Agreement.",
    "3. AMENDMENT TO TERMINATION FOR CONVENIENCE\n\n"
    "3.1 Section 10.1 of the Agreement is deleted and replaced with the following: "
    '"Customer may terminate this Agreement for convenience at any time after the '
    "first twenty-four (24) months of the Initial Term by giving Supplier not less "
    "than ninety (90) days' written notice.\"\n\n"
    "4. NO OTHER CHANGES\n\n"
    "4.1 Except as expressly amended by this Amendment, the Agreement remains in full "
    "force and effect in accordance with its terms.\n\n"
    "4.2 In the event of any conflict between this Amendment and the Agreement, this "
    "Amendment prevails.\n\n"
    "IN WITNESS WHEREOF, the parties have executed this Amendment as of the date "
    "first written above.",
]

NDA_PAGES: list[str] = [
    "MUTUAL NON-DISCLOSURE AGREEMENT\n\n"
    "This Mutual Non-Disclosure Agreement is made as of 2 February 2026 between "
    'Northwind Logistics, Inc. and Arbor Systems LLC (each a "Party" and together the '
    '"Parties").\n\n'
    "1. PURPOSE\n\n"
    "1.1 The Parties wish to explore a potential commercial relationship concerning "
    'warehouse management software (the "Purpose") and, in doing so, each may disclose '
    "to the other information that is confidential or proprietary.\n\n"
    "2. CONFIDENTIAL INFORMATION\n\n"
    '2.1 "Confidential Information" means any information disclosed by one Party to '
    "the other in connection with the Purpose, including commercial terms, technical "
    "specifications, customer lists, pricing and business plans.\n\n"
    "3. OBLIGATIONS\n\n"
    "3.1 The receiving Party shall use the disclosing Party's Confidential Information "
    "solely for the Purpose and shall not disclose it to any third party without the "
    "disclosing Party's prior written consent.\n\n"
    "3.2 The obligations in this Agreement continue for a period of three (3) years "
    "from the date of disclosure of the Confidential Information to which they relate.",
    "4. RETURN OF MATERIALS\n\n"
    "4.1 On the written request of the disclosing Party, the receiving Party shall "
    "return or destroy all materials containing Confidential Information, save that it "
    "may retain one copy for the sole purpose of demonstrating compliance with its "
    "obligations under this Agreement.\n\n"
    "5. NO LICENCE\n\n"
    "5.1 Nothing in this Agreement grants either Party any licence or other right in "
    "respect of the other's intellectual property.\n\n"
    "6. NO OBLIGATION TO PROCEED\n\n"
    "6.1 Nothing in this Agreement obliges either Party to enter into any further "
    "agreement or to proceed with the Purpose.\n\n"
    "7. GOVERNING LAW\n\n"
    "7.1 This Agreement is governed by the laws of the State of New York.",
]


@dataclass(frozen=True)
class EvalDocument:
    key: str
    title: str
    filename: str
    pages: list[str]


DOCUMENTS: tuple[EvalDocument, ...] = (
    EvalDocument(
        key="msa",
        title="Master Services Agreement — Northwind / Arbor Systems",
        filename="northwind-arbor-msa.pdf",
        pages=MSA_PAGES,
    ),
    EvalDocument(
        key="amendment",
        title="Amendment No. 2 to Master Services Agreement",
        filename="northwind-arbor-msa-amendment-2.pdf",
        pages=AMENDMENT_PAGES,
    ),
    EvalDocument(
        key="nda",
        title="Mutual Non-Disclosure Agreement — Northwind / Arbor Systems",
        filename="northwind-arbor-mutual-nda.pdf",
        pages=NDA_PAGES,
    ),
)
