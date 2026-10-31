export type Intent = "ask" | "summary" | "draft";
export type MessageRole = "user" | "assistant";
export type IngestStatus = "queued" | "processing" | "ready" | "failed";

export interface Matter {
  id: string;
  name: string;
  client: string;
}

export interface LegalDocument {
  id: string;
  matterId: string;
  title: string;
  filename: string;
  kind: "msa" | "nda" | "complaint" | "other";
  pages: number;
  sizeBytes: number;
  status: IngestStatus;
  /** Present when status === "failed" — e.g. the FR-2 scanned-PDF rejection. */
  failureReason?: string;
  /**
   * Where the page text came from.
   *
   * `"ocr"` means it was recovered by transcription rather than read from a text
   * layer — so a citation into this document quotes what the OCR engine produced,
   * not what the document provably says. That is weaker evidence than a native
   * citation and the viewer should say so; defaults to `"native"` for every
   * document ingested before OCR existed.
   */
  textSource?: "native" | "ocr";
  uploadedAt: string;
}

/**
 * One page of a document's extracted text layer.
 *
 * Page-granular because a citation resolves to a page and the viewer opens to one.
 * This is what the backend's GET /documents/:id/page/:n returns, and it is what
 * citation highlighting is resolved against — the span is found in this text.
 */
export interface DocumentPage {
  documentId: string;
  page: number;
  text: string;
  charCount: number;
}

export interface Citation {
  /** Stable id referenced by {{cite:n}} markers in message content. */
  n: number;
  documentId: string;
  documentTitle: string;
  page: number;
  citedText: string;
}

export interface SummaryField {
  key: string;
  label: string;
  /** null means the clause is absent from the document — rendered explicitly. */
  value: string | null;
  cites: number[];
}

export interface DraftPayload {
  title: string;
  /** Markdown; [ATTORNEY INPUT REQUIRED] markers are styled on render. */
  body: string;
}

export interface Message {
  id: string;
  conversationId: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  intent?: Intent;
  citations?: Citation[];
  grounded?: boolean;
  /** True when the documents don't support an answer — a correct outcome, not an error. */
  abstained?: boolean;
  summary?: SummaryField[];
  draft?: DraftPayload;
  scopedDocumentIds?: string[];
}

export interface Conversation {
  id: string;
  title: string;
  matterId: string;
  createdAt: string;
  updatedAt: string;
  lastIntent?: Intent;
}

/**
 * `GET /api/health`.
 *
 * `degraded` never means the service is down — a missing model key degrades
 * generation while every read path still works, and the UI must be able to tell the
 * difference. `checks` names which capability is missing; `status` only says one is.
 */
export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  environment: string;
  checks: Record<string, string>;
}
