import type { DocumentPage } from "@/types/domain";
import { documents } from "./documents";
import { messagesByConversation } from "./messages";

/**
 * Page text for the document viewer.
 *
 * The one hard requirement: **every cited span must appear verbatim on the page its
 * citation names.** Highlighting works by finding `citedText` in this text, so a fixture
 * that paraphrased would produce a chip that opens the right page and highlights
 * nothing — which is exactly the silent failure the product exists to prevent.
 *
 * So the spans are not written here. They are collected from the citations in
 * `messages.ts` and inserted verbatim, which makes drift impossible: change a citation
 * and the page it points at changes with it.
 */

/** doc id → page → the spans cited on that page, in citation order. */
const citedSpans: Record<string, Record<number, string[]>> = {};
for (const messages of Object.values(messagesByConversation)) {
  for (const message of messages) {
    for (const c of message.citations ?? []) {
      const byPage = (citedSpans[c.documentId] ??= {});
      const spans = (byPage[c.page] ??= []);
      if (!spans.includes(c.citedText)) spans.push(c.citedText);
    }
  }
}

interface Section {
  from: number;
  to: number;
  article: string;
  title: string;
}

/** Enough of an outline that paging through a document reads like paging through one. */
const OUTLINES: Record<string, Section[]> = {
  "d-msa": [
    { from: 1, to: 1, article: "", title: "Master Services Agreement" },
    { from: 2, to: 2, article: "ARTICLE 1", title: "Definitions" },
    { from: 3, to: 6, article: "ARTICLE 2", title: "Term and Renewal" },
    { from: 7, to: 11, article: "ARTICLE 3", title: "Services and Statements of Work" },
    { from: 12, to: 15, article: "ARTICLE 4", title: "Fees, Invoicing and Payment" },
    { from: 16, to: 21, article: "ARTICLE 8", title: "Confidentiality" },
    { from: 22, to: 27, article: "ARTICLE 9", title: "Data Protection and Security" },
    { from: 28, to: 32, article: "ARTICLE 12", title: "Term and Termination" },
    { from: 33, to: 35, article: "ARTICLE 13", title: "Limitation of Liability" },
    { from: 36, to: 37, article: "ARTICLE 16", title: "Governing Law and Dispute Resolution" },
    { from: 38, to: 41, article: "ARTICLE 17", title: "General Provisions" },
    { from: 42, to: 42, article: "", title: "Signature Page" },
  ],
  "d-nda": [
    { from: 1, to: 1, article: "", title: "Mutual Non-Disclosure Agreement" },
    { from: 2, to: 2, article: "SECTION 1", title: "Definition of Confidential Information" },
    { from: 3, to: 3, article: "SECTION 2", title: "Obligations of the Receiving Party" },
    { from: 4, to: 4, article: "SECTION 3", title: "Exclusions" },
    { from: 5, to: 5, article: "SECTION 4", title: "Term and Return of Materials" },
    { from: 6, to: 6, article: "SECTION 5", title: "Governing Law and Venue" },
    { from: 7, to: 7, article: "SECTION 6", title: "Remedies" },
    { from: 8, to: 8, article: "", title: "Signature Page" },
  ],
  "d-complaint": [
    { from: 1, to: 1, article: "", title: "Complaint for Damages" },
    { from: 2, to: 2, article: "I.", title: "The Parties" },
    { from: 3, to: 4, article: "II.", title: "Jurisdiction and Venue" },
    { from: 5, to: 8, article: "III.", title: "Factual Allegations" },
    { from: 9, to: 12, article: "IV.", title: "Causes of Action" },
    { from: 13, to: 13, article: "V.", title: "Damages" },
    { from: 14, to: 14, article: "VI.", title: "Prayer for Relief" },
    { from: 15, to: 15, article: "VII.", title: "Demand for Jury Trial" },
  ],
  "d-amend": [
    { from: 1, to: 1, article: "", title: "Amendment No. 2" },
    { from: 2, to: 3, article: "SECTION 1", title: "Amended Terms" },
    { from: 4, to: 5, article: "SECTION 2", title: "Revised Exhibit B" },
    { from: 6, to: 6, article: "", title: "Signature Page" },
  ],
};

const CONTRACT_SENTENCES = [
  "Each party shall comply with all applicable laws, regulations and professional standards in the performance of its obligations under this Agreement.",
  "No delay or omission by either party in exercising any right under this Agreement shall operate as a waiver of that right or of any other right.",
  "The parties acknowledge that the fees set forth in each Statement of Work were established in reliance on the allocation of risk expressed in this Article.",
  "Any notice required under this Agreement shall be given in writing and shall be deemed received upon personal delivery, upon confirmed electronic transmission, or three (3) business days after deposit with a nationally recognised courier.",
  "Provider shall maintain complete and accurate records relating to the Services for a period of three (3) years following the termination or expiration of the applicable Statement of Work.",
  "Client shall designate a single point of contact authorised to approve deliverables and to resolve escalations arising in the ordinary course of the engagement.",
  "Neither party shall be liable for any failure or delay in performance to the extent caused by circumstances beyond its reasonable control, provided that the affected party gives prompt notice and uses commercially reasonable efforts to resume performance.",
  "The headings in this Agreement are for convenience of reference only and shall not affect the interpretation of any provision.",
  "If any provision of this Agreement is held unenforceable, that provision shall be modified to the minimum extent necessary to render it enforceable, and the remaining provisions shall continue in full force and effect.",
  "Provider shall not assign this Agreement or any Statement of Work, in whole or in part, without the prior written consent of Client, except to a successor in interest by merger or acquisition of substantially all of its assets.",
  "Each party represents that it has full corporate power and authority to enter into this Agreement and that its execution has been duly authorised by all necessary corporate action.",
  "The parties intend that this Agreement be construed without presumption against the party that drafted it.",
  "Amounts properly disputed in good faith shall not be subject to late-payment interest while the dispute remains pending, provided that the undisputed portion of the invoice is paid when due.",
  "Provider shall ensure that each individual assigned to perform Services has executed a written agreement containing confidentiality obligations consistent with this Article.",
  "The obligations in this Article shall survive for a period of five (5) years following termination, except with respect to trade secrets, as to which the obligations shall survive indefinitely.",
  "Nothing in this Agreement creates a partnership, joint venture, agency or employment relationship between the parties.",
];

const PLEADING_SENTENCES = [
  "Plaintiff is informed and believes, and on that basis alleges, that Defendant was at all relevant times doing business within this judicial district.",
  "Defendant represented that the components conformed to the specifications set forth in the purchase orders attached hereto as Exhibit A.",
  "Plaintiff performed each of its obligations under the agreement except those excused or prevented by Defendant's conduct.",
  "Despite repeated written demands, Defendant has failed and refused to cure the nonconformities described above.",
  "As a direct and proximate result of Defendant's conduct, Plaintiff has suffered damages in an amount to be proven at trial.",
  "Plaintiff reserves the right to amend this pleading upon completion of discovery into the matters alleged herein.",
  "The acts and omissions alleged above were a substantial factor in causing the harm for which Plaintiff seeks relief.",
  "Plaintiff is entitled to recover its reasonable attorneys' fees and costs pursuant to the fee-shifting provision of the agreement.",
];

/** Deterministic, so a page looks the same on every reload and in every screenshot. */
function seed(docId: string, page: number) {
  let h = 2166136261;
  for (const ch of `${docId}:${page}`) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

function sectionFor(docId: string, page: number): Section | undefined {
  return OUTLINES[docId]?.find((s) => page >= s.from && page <= s.to);
}

function buildPage(docId: string, page: number): string {
  const doc = documents.find((d) => d.id === docId);
  const section = sectionFor(docId, page);
  const pleading = doc?.kind === "complaint";
  const pool = pleading ? PLEADING_SENTENCES : CONTRACT_SENTENCES;
  const rand = seed(docId, page);
  const lines: string[] = [];

  // Page 1 is a cover or caption, not clause text.
  if (page === 1) {
    if (pleading) {
      lines.push(
        "SUPERIOR COURT OF THE STATE OF CALIFORNIA\nCOUNTY OF SAN FRANCISCO\n\nCALDER INDUSTRIAL GROUP, INC.,\n    Plaintiff,\n\n        v.\n\nBRIGHTPOINT MANUFACTURING LLC,\n    Defendant.\n\nCase No. CGC-26-118402\n\nCOMPLAINT FOR BREACH OF CONTRACT,\nBREACH OF WARRANTY AND\nNEGLIGENT MISREPRESENTATION",
      );
    } else {
      lines.push(`${doc?.title ?? "Agreement"}`);
      lines.push(
        "This Agreement is made as of the Effective Date set forth below. The parties, intending to be legally bound, agree as follows.",
      );
    }
  } else if (section) {
    lines.push(`${section.article} ${section.title}`.trim().toUpperCase());
  }

  const spans = citedSpans[docId]?.[page] ?? [];
  const clauseBase = section?.article.match(/\d+/)?.[0] ?? "1";

  // One lead paragraph, then the cited spans as their own clauses, then filler. Cited
  // spans land above the fold of a tall page so the scroll-into-view has somewhere to go.
  let clause = 1;
  const emit = (body: string, label?: string) => {
    if (pleading) {
      lines.push(`${(page - 1) * 4 + clause}.    ${body}`);
    } else {
      lines.push(`${clauseBase}.${clause}${label ? ` ${label}` : ""}  ${body}`);
    }
    clause += 1;
  };

  if (page > 1) emit(pool[Math.floor(rand() * pool.length)]);
  for (const span of spans) emit(span);
  const filler = page === 1 ? 2 : 4;
  for (let i = 0; i < filler; i++) emit(pool[Math.floor(rand() * pool.length)]);

  return lines.join("\n\n");
}

const cache = new Map<string, DocumentPage>();

/** What GET /documents/:id/page/:n returns. Memoised so repeat visits are identical. */
export function documentPage(documentId: string, page: number): DocumentPage | null {
  const doc = documents.find((d) => d.id === documentId);
  if (!doc || page < 1 || page > doc.pages) return null;

  const key = `${documentId}:${page}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const text = buildPage(documentId, page);
  const built: DocumentPage = { documentId, page, text, charCount: text.length };
  cache.set(key, built);
  return built;
}
