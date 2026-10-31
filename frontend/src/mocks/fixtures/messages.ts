import type { Citation, Message, SummaryField } from "@/types/domain";

const h = (n: number) => new Date(Date.now() - n * 3_600_000).toISOString();

const msaCite = (n: number, page: number, text: string): Citation => ({
  n,
  documentId: "d-msa",
  documentTitle: "Master Services Agreement — Northwind / Arbor Systems",
  page,
  citedText: text,
});

const summaryFields: SummaryField[] = [
  {
    key: "parties",
    label: "Parties",
    value:
      "Northwind Logistics, Inc. (a Delaware corporation, “Client”) and Arbor Systems LLC (a California limited liability company, “Provider”).",
    cites: [1],
  },
  {
    key: "term",
    label: "Term & renewal",
    value:
      "Initial term of thirty-six (36) months from the Effective Date of 1 March 2025, renewing automatically for successive twelve-month periods unless either party gives written notice of non-renewal at least ninety (90) days before the end of the then-current term.",
    cites: [2],
  },
  {
    key: "obligations",
    label: "Obligations",
    value:
      "Provider to deliver the services described in each Statement of Work to the service levels in Exhibit B. Client to furnish data, access and designated personnel, and to review deliverables within ten (10) business days of submission.",
    cites: [3],
  },
  {
    key: "payment",
    label: "Payment terms",
    value:
      "Net forty-five (45) days from receipt of a conforming invoice. Late amounts accrue interest at 1.0% per month. Fees may be increased once per renewal term on sixty (60) days’ notice, capped at the lesser of 5% or CPI-U.",
    cites: [4],
  },
  {
    key: "termination",
    label: "Termination triggers",
    value:
      "Either party may terminate for material breach uncured after thirty (30) days’ written notice. Client may terminate any Statement of Work for convenience on sixty (60) days’ notice, subject to payment for work performed and non-cancellable commitments.",
    cites: [5],
  },
  {
    key: "governingLaw",
    label: "Governing law & venue",
    value:
      "Governed by the laws of the State of Delaware without regard to conflict-of-laws principles. Exclusive venue in the state and federal courts located in New Castle County, Delaware; both parties waive trial by jury.",
    cites: [6],
  },
  {
    key: "liability",
    label: "Liability & indemnification",
    value:
      "Aggregate liability capped at the fees paid in the twelve (12) months preceding the claim. Cap does not apply to indemnification obligations, breach of confidentiality, or gross negligence and wilful misconduct.",
    cites: [7],
  },
  {
    key: "unusual",
    label: "Unusual provisions",
    value:
      "§14.3 grants Provider a perpetual, royalty-free licence to any feedback Client supplies, with no restriction on use. §9.2 permits unilateral amendment of the security exhibit on thirty (30) days’ notice. Both are off-market for an agreement of this type and worth negotiating.",
    cites: [8, 9],
  },
  {
    key: "openItems",
    label: "Open items",
    value: null,
    cites: [],
  },
];

export const messagesByConversation: Record<string, Message[]> = {
  "c-term": [
    {
      id: "m-term-u",
      conversationId: "c-term",
      role: "user",
      content: "What does this contract say about early termination?",
      createdAt: h(2),
      scopedDocumentIds: ["d-msa"],
    },
    {
      id: "m-term-a",
      conversationId: "c-term",
      role: "assistant",
      intent: "ask",
      grounded: true,
      createdAt: h(2),
      content: `The agreement provides two distinct routes to early termination, and they carry very different consequences.

**Termination for cause.** Either party may terminate if the other commits a material breach that remains uncured thirty (30) days after written notice describing the breach in reasonable detail.{{cite:1}} No termination fee applies.

**Termination for convenience.** Client may terminate any individual Statement of Work on sixty (60) days' written notice without terminating the Agreement itself.{{cite:2}} This route requires Client to pay for services performed through the effective date **and** for non-cancellable third-party commitments Provider has already incurred.{{cite:3}}

Two points worth flagging:

- Termination for convenience is available to **Client only**. Provider has no equivalent right.{{cite:2}}
- Terminating every Statement of Work does not terminate the Agreement — the master terms, including the confidentiality and indemnification provisions, survive until the Agreement itself is terminated or expires.{{cite:4}}`,
      citations: [
        msaCite(1, 28, "Either party may terminate this Agreement for material breach by the other party which remains uncured thirty (30) days following written notice describing such breach in reasonable detail."),
        msaCite(2, 29, "Client may terminate any Statement of Work for convenience upon sixty (60) days' prior written notice to Provider."),
        msaCite(3, 29, "Upon termination for convenience, Client shall pay Provider for all Services performed through the effective date of termination and for any non-cancellable commitments to third parties reasonably incurred by Provider."),
        msaCite(4, 30, "Termination of all Statements of Work shall not operate to terminate this Agreement. Sections 8 (Confidentiality), 11 (Indemnification) and 13 (Limitation of Liability) survive any termination or expiration."),
      ],
    },
  ],

  "c-insure": [
    {
      id: "m-ins-u",
      conversationId: "c-insure",
      role: "user",
      content: "What are the insurance requirements Provider has to maintain?",
      createdAt: h(96),
      scopedDocumentIds: ["d-msa", "d-nda"],
    },
    {
      id: "m-ins-a",
      conversationId: "c-insure",
      role: "assistant",
      intent: "ask",
      abstained: true,
      grounded: true,
      createdAt: h(96),
      content: `The documents in scope do not address insurance requirements.

I searched both the Master Services Agreement and the Mutual Non-Disclosure Agreement. Neither contains an insurance provision, a certificate-of-insurance requirement, or any reference to coverage minimums, additional-insured status, or waiver of subrogation.

For an agreement of this size, the absence of an insurance article is itself notable — it may have been moved to a separate exhibit that has not been uploaded, or omitted entirely.`,
      citations: [],
    },
  ],

  "c-indem": [
    {
      id: "m-ind-u",
      conversationId: "c-indem",
      role: "user",
      content: "Walk me through the indemnification caps and any carve-outs.",
      createdAt: h(31),
      scopedDocumentIds: ["d-msa"],
    },
    {
      id: "m-ind-a",
      conversationId: "c-indem",
      role: "assistant",
      intent: "ask",
      grounded: true,
      createdAt: h(31),
      content: `Aggregate liability is capped at **the total fees paid by Client in the twelve (12) months immediately preceding the event giving rise to the claim**.{{cite:1}}

The cap is subject to four carve-outs, which are uncapped:

| Carve-out | Source |
|---|---|
| Indemnification obligations under §11 | §13.2(a) |
| Breach of confidentiality under §8 | §13.2(b) |
| Gross negligence or wilful misconduct | §13.2(c) |
| Client's payment obligations | §13.2(d) |

Consequential damages are mutually excluded, but that exclusion is **also** subject to the same four carve-outs{{cite:2}} — so an indemnified third-party claim could reach consequential losses without limit.`,
      citations: [
        msaCite(1, 33, "Except as set forth in Section 13.2, each party's aggregate liability arising out of or related to this Agreement shall not exceed the total fees paid by Client hereunder in the twelve (12) months immediately preceding the event giving rise to such liability."),
        msaCite(2, 34, "The exclusions and limitations in Sections 13.1 and 13.3 shall not apply to the matters described in Section 13.2(a) through (d)."),
      ],
    },
  ],

  "c-summary": [
    {
      id: "m-sum-u",
      conversationId: "c-summary",
      role: "user",
      content: "Summarise the Northwind / Arbor MSA.",
      createdAt: h(6),
      scopedDocumentIds: ["d-msa"],
    },
    {
      id: "m-sum-a",
      conversationId: "c-summary",
      role: "assistant",
      intent: "summary",
      grounded: true,
      createdAt: h(5),
      content: "",
      summary: summaryFields,
      citations: [
        msaCite(1, 1, "This Master Services Agreement is entered into by and between Northwind Logistics, Inc., a Delaware corporation, and Arbor Systems LLC, a California limited liability company."),
        msaCite(2, 3, "The initial term shall be thirty-six (36) months commencing on the Effective Date and shall renew automatically for successive twelve (12) month periods."),
        msaCite(3, 7, "Provider shall perform the Services described in each Statement of Work in accordance with the service levels set forth in Exhibit B."),
        msaCite(4, 12, "Client shall pay each undisputed invoice within forty-five (45) days of receipt."),
        msaCite(5, 28, "Either party may terminate this Agreement for material breach which remains uncured thirty (30) days following written notice."),
        msaCite(6, 36, "This Agreement shall be governed by the laws of the State of Delaware. The parties consent to exclusive jurisdiction and venue in New Castle County, Delaware and waive any right to trial by jury."),
        msaCite(7, 33, "Each party's aggregate liability shall not exceed the total fees paid in the twelve (12) months preceding the event giving rise to such liability."),
        msaCite(8, 38, "Client grants Provider a perpetual, irrevocable, royalty-free licence to use any Feedback for any purpose without restriction or attribution."),
        msaCite(9, 22, "Provider may amend Exhibit D (Security Requirements) upon thirty (30) days' written notice to Client."),
      ],
    },
  ],

  "c-draft": [
    {
      id: "m-dr-u",
      conversationId: "c-draft",
      role: "user",
      content:
        "Draft a memo on whether the non-compete in the MSA is enforceable against Arbor's subcontractors.",
      createdAt: h(27),
      scopedDocumentIds: ["d-msa"],
    },
    {
      id: "m-dr-a",
      conversationId: "c-draft",
      role: "assistant",
      intent: "draft",
      grounded: true,
      createdAt: h(26),
      content: "",
      draft: {
        title: "Memorandum — Enforceability of §15.4 Against Provider Subcontractors",
        body: `**TO:** [ATTORNEY INPUT REQUIRED — supervising partner]
**FROM:** Legal Lens (unreviewed draft)
**RE:** Enforceability of the §15.4 restrictive covenant against Arbor Systems' subcontractors
**DATE:** ${new Date().toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" })}

## Question Presented

Whether the restrictive covenant at §15.4 of the Master Services Agreement binds subcontractors engaged by Provider, where those subcontractors are not signatories to the Agreement.

## Brief Answer

Probably not as drafted, for two reasons apparent on the face of the document. First, §15.4 by its terms restricts only "Provider," a defined term that does not extend to subcontractors.{{cite:1}} Second, the flow-down provision at §6.3 obliges Provider to bind subcontractors to the confidentiality article but is silent as to §15.4.{{cite:2}} A definitive answer requires analysis of controlling authority on third-party enforcement of restrictive covenants, which is outside the scope of this draft.

[ATTORNEY INPUT REQUIRED — governing-law analysis under Delaware authority]

## Facts

The Agreement defines "Provider" as Arbor Systems LLC alone, without reference to affiliates, agents, or subcontractors.{{cite:1}} Section 6.3 permits Provider to engage subcontractors with Client's prior written consent and requires Provider to bind each subcontractor in writing to obligations "no less protective than those in Section 8 (Confidentiality)."{{cite:2}} The restrictive covenant at §15.4 prohibits Provider, for twelve months following termination, from soliciting Client personnel or providing substantially similar services to three named competitors.{{cite:3}}

## Discussion

The flow-down at §6.3 is expressly limited to the confidentiality article. Where an agreement enumerates which obligations flow down and omits others, the omission is ordinarily read as deliberate.

[ATTORNEY INPUT REQUIRED — citation to controlling authority on expressio unius in commercial agreements]

Client's practical exposure is therefore that a subcontractor could perform for a named competitor immediately on termination without breaching any obligation it owes Client directly, while Provider itself remains restricted.

## Conclusion

Section 15.4 likely does not reach Provider's subcontractors as currently drafted. If Client intends the covenant to bind subcontractors, §6.3 should be amended to flow down §15.4 expressly.

[ATTORNEY INPUT REQUIRED — recommendation on amendment strategy and negotiating posture]`,
      },
      citations: [
        msaCite(1, 2, '"Provider" means Arbor Systems LLC, a California limited liability company.'),
        msaCite(2, 10, "Provider may engage subcontractors with Client's prior written consent, and shall bind each such subcontractor in writing to obligations no less protective than those set forth in Section 8 (Confidentiality)."),
        msaCite(3, 39, "For a period of twelve (12) months following termination, Provider shall not solicit for employment any Client personnel nor provide substantially similar services to Meridian Freight, Castlegate Holdings or Pellar Transit."),
      ],
    },
  ],

  "c-venue": [
    {
      id: "m-ven-u",
      conversationId: "c-venue",
      role: "user",
      content: "Do the MSA and the NDA agree on governing law and venue?",
      createdAt: h(220),
      scopedDocumentIds: ["d-msa", "d-nda"],
    },
    {
      id: "m-ven-a",
      conversationId: "c-venue",
      role: "assistant",
      intent: "ask",
      grounded: true,
      createdAt: h(219),
      content: `No — they conflict, and the conflict is material.

The Master Services Agreement selects **Delaware** law with exclusive venue in New Castle County and a mutual jury waiver.{{cite:1}}

The Mutual NDA selects **California** law with exclusive venue in San Francisco County, and contains no jury waiver.{{cite:2}}

Because the NDA predates the MSA and the MSA's integration clause is limited to "the subject matter hereof,"{{cite:3}} the NDA's clause is not obviously superseded. A confidentiality dispute could plausibly be litigated in California under California law, before a jury — which is unlikely to be what either party intended.`,
      citations: [
        msaCite(1, 36, "This Agreement shall be governed by the laws of the State of Delaware. The parties consent to exclusive jurisdiction and venue in New Castle County, Delaware and waive any right to trial by jury."),
        {
          n: 2,
          documentId: "d-nda",
          documentTitle: "Mutual Non-Disclosure Agreement — Northwind / Arbor Systems",
          page: 6,
          citedText:
            "This Agreement shall be governed by the laws of the State of California. The parties submit to the exclusive jurisdiction of the state and federal courts located in San Francisco County, California.",
        },
        msaCite(3, 40, "This Agreement constitutes the entire agreement between the parties with respect to the subject matter hereof and supersedes all prior agreements relating to such subject matter."),
      ],
    },
  ],
};
