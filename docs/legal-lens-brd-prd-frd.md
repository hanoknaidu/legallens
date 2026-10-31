# Legal Lens — Product & Functional Specification

**Hybrid BRD / PRD / FRD** · v0.1 Draft · 2026-10-01 · Owner: H. Suravar · *Rev 0.1 — initial draft from `ideas.md`*

> **Course project.** Legal Lens is a teaching vehicle, not a commercial product. It is specified and engineered to production standards so the specification itself is instructive; it is not deployed to real attorneys or real client matters.

---

## 1. About This Document

This document does the work usually split across three: a **BRD** (why build it — §2), a **PRD** (what it does for users — §3), and an **FRD** (what the system must do — §5–§6). It is written for software engineers who are not lawyers; legal terms are glossed inline and in §11.

**Not in scope here:** vector database and embedding model selection, chunking strategy, prompt design. Those belong to the Technical Architecture Document that follows. §4.3 specifies the *interfaces and selection criteria* those choices must satisfy, so deferring them leaves no gap in the requirements.

---

## 2. Business Context

### 2.1 Problem

Legal work is document-bound in a way most knowledge work is not. A single **matter** (one case or transaction) can carry hundreds of pages of contracts, pleadings, and discovery. Three costs recur:

- **Retrieval** — finding the one indemnification clause that governs a dispute is a manual scan.
- **Repetition** — "who are the parties, what are the obligations, when does it terminate, what is unusual" is the same task performed on every new document.
- **Blank page** — memos and case summaries start from scratch.

### 2.2 Why general-purpose AI fails here

A general chatbot is unusable for legal work for one specific reason: **it will confidently invent a citation.** Not hypothetical — attorneys have been sanctioned for filing briefs citing cases that do not exist. A professional whose license is at risk cannot verify an answer that does not show its source.

So the differentiating constraint is not *answer well*. It is **answer only from the documents provided, and show exactly where each answer came from.** Every decision below follows from that.

### 2.3 Success metrics

No commercial context exists, so revenue and seat metrics would be theater. Success is product quality:

| Metric | Target | Why it matters |
|---|---|---|
| Citation accuracy | ≥ 95% of cited passages genuinely support the claim | The core trust property |
| Correct abstention | ≥ 90% of unanswerable questions answered "not found" | Refusing to guess is the hardest and most important behavior |
| Time to answer | p95 < 15 s on a 100-page document | Below the point where a user would just scan manually |
| Summary completeness | All 9 fields populated or explicitly marked absent | A missing clause matters as much as a present one |

---

## 3. Product Definition

### 3.1 Users

| | Persona | Need |
|---|---|---|
| Primary | Practicing attorney, solo or small firm | Trustworthy answers about their own documents; an editable first draft |
| Secondary | Paralegal | Document triage and summarization at volume |
| Explicitly not | End clients, general public | A tool *for* licensed professionals, not a legal-advice service (§8.3) |

### 3.2 The three features

**Ask.** The user uploads documents and types a question. They get a direct answer composed only of what the documents say, each assertion carrying a clickable citation to a page. If the documents do not answer the question, the system says so and offers nothing in its place.

**Summary.** The user selects a document and gets a **structured** summary, not prose — lawyers read for fields, and fields are comparable across documents in a way paragraphs are not. Nine fields: parties; term and renewal; obligations per party; payment terms; termination triggers; governing law and venue; liability and indemnification; unusual or off-market provisions; open items needing human review. Each populated field cites its source page; expected-but-absent clauses are reported explicitly.

**Draft.** The user describes what they need and selects source documents. They get a structured first draft — for a memo: Question Presented / Brief Answer / Facts / Discussion / Conclusion — with facts cited to sources. Where a fact is missing it marks `[ATTORNEY INPUT REQUIRED]` rather than inventing a plausible specific.

### 3.3 Phase 1 boundaries

| In | Out |
|---|---|
| Uploaded documents as the only knowledge source | Case law, statutes, regulations (Phase 3) |
| Single user, no login | Authentication, multi-user, firm sharing (Phase 2) |
| Descriptions of what documents say | Legal advice on what to do |
| Draft as editable, exportable text | Any outbound action — filing, sending, serving (never) |
| Native-text PDF, DOCX, TXT | Scanned PDFs — rejected explicitly, never silently mis-ingested |

---

## 4. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  BROWSER — React + TypeScript + Vite                          │
│  Upload · Prompt bar · Answer+citations · PDF viewer/highlight │
└────────────────────────────┬──────────────────────────────────┘
                             │ HTTPS/JSON · SSE for token streaming
┌────────────────────────────┴──────────────────────────────────┐
│  API — Python · FastAPI · uv                                  │
│  /matters  /documents  /ask  /summary  /draft  /audit          │
├───────────────────────────────────────────────────────────────┤
│  AI LAYER                                                      │
│   ingest:  extract → chunk → embed ─────────────► vector store │
│   query:   embed → retrieve ──┐                                │
│                               ▼                                │
│                      prompt assembly ──► Claude API            │
│                                          (citations enabled)    │
└────────────────────────────┬──────────────────────────────────┘
                             │
                  ┌──────────┴──────────┐
                  │ Postgres │ Object   │
                  │ metadata │ store    │
                  └──────────┴──────────┘
```

### 4.1 Citations are infrastructure, not a feature to build

The Claude API supports native citations: mark a document as citable, and the response returns as text blocks where each cited block carries a `citations` array with the exact `cited_text`, the document title, and a 1-indexed page range. "Click a claim, see the highlighted source passage" is therefore **supported platform behavior**, not a bespoke string-matching system. Given that fabricated citations are this product's catastrophic failure mode, this is the most load-bearing technical fact in the specification.

One constraint follows: citations are **incompatible with structured JSON output** in a single API call. Summary wants both — resolution in §9.

### 4.2 Design decision — retrieval strategy

RAG is the specified architecture. The honest engineering observation is that **Phase 1 may not need it.** A 100-page contract is roughly 75,000 tokens; the context window is 1,000,000. The document fits whole — and a whole document in context cannot suffer a retrieval miss, produces page citations natively, and can answer *"there is no limitation of liability clause"*: a question about **absence**, which retrieval is structurally bad at, because retrieval returns what matches and cannot return what is missing.

Retrieval earns its place when the corpus genuinely exceeds the context window. The specification therefore sets a **threshold**, not a single pipeline:

| Corpus for the query | Path |
|---|---|
| Fits in context (Phase 1's common case) | Whole document(s) in context + prompt caching + native citations |
| Exceeds context, or cost-bound at volume | RAG retrieval over the vector store |

Both paths are built; the threshold selects per request. This is more instructive than either alone: *the right tool depends on the size of the problem, and a 1M-token context window moved where that boundary sits.*

### 4.3 Deferred choices and the bar they must clear

| Choice | Must satisfy |
|---|---|
| Vector store | Metadata filtering by `matter_id`; hybrid dense + keyword search; verifiable hard delete |
| Embedding model | Contractual no-retention/no-training posture — it processes privileged text (§8.2); ≥ 8K token input |
| Chunking strategy | Respects clause and section boundaries; preserves defined-term context |

**Portability is a requirement, not an aspiration** (NFR-11): model provider, embedding provider, and vector store each sit behind an interface, so swapping one changes no API contract and no frontend code.

---

## 5. Functional Requirements

| Area | ID | Requirement | Pri |
|---|---|---|---|
| Documents | FR-1 | Upload PDF, DOCX, TXT; ≤ 50 MB and ≤ 500 pages per document | MUST |
| | FR-2 | Detect a PDF with no extractable text layer and reject it, naming OCR as unsupported — never ingest it as empty | MUST |
| | FR-3 | List, view, delete documents; deletion purges derived artifacts (NFR-8) | MUST |
| | FR-4 | Every document belongs to a `matter`; a default matter is auto-created in Phase 1 | MUST |
| | FR-5 | Expose ingestion state (`queued`/`processing`/`ready`/`failed`) with a failure reason | MUST |
| Ask | FR-6 | Accept a natural-language question scoped to a selected document or matter | MUST |
| | FR-7 | Every factual assertion carries ≥ 1 citation with document title and page number | MUST |
| | FR-8 | Clicking a citation opens the source at that page with the passage highlighted | MUST |
| | FR-9 | When the documents do not support an answer, say so and supply no substitute | MUST |
| | FR-10 | Stream answers incrementally; support multi-turn follow-ups and cross-document questions within a matter | SHOULD |
| Summary | FR-11 | Produce the 9-field structured summary of §3.2, identically shaped across documents | MUST |
| | FR-12 | Cite source page(s) for each populated field | MUST |
| | FR-13 | Report expected-but-absent clauses explicitly rather than omitting them | MUST |
| Draft | FR-14 | Generate a memo or case summary from an instruction plus selected source documents | MUST |
| | FR-15 | Use conventional structure; cite facts to source documents | MUST |
| | FR-16 | Mark missing facts `[ATTORNEY INPUT REQUIRED]`; never invent a specific to fill a gap | MUST |
| | FR-17 | Label output as an unreviewed draft in the UI and inside the exported file | MUST |
| | FR-18 | Export to `.docx` and Markdown | MUST |
| | FR-19 | Perform no outbound action — no filing, sending, serving — in any phase | MUST |
| Audit | FR-20 | Log every generation: timestamp, feature, question, document IDs, grounding spans, model ID, token counts, latency | MUST |

---

## 6. Non-Functional Requirements

| ID | Requirement | Target / verification |
|---|---|---|
| NFR-1 | **Groundedness** — no uncited factual assertion in Ask or Summary | Held-out set of 50 `(document, question, expected answer, expected citation)` cases including ≥ 15 unanswerable ones: ≥ 95% citation accuracy, ≥ 90% correct abstention. **Built before prompt tuning begins** |
| NFR-2 | **Latency** | p95 first token < 3 s; p95 complete Ask < 15 s; Summary < 45 s; Draft < 90 s |
| NFR-3 | **Ingestion** | 100-page PDF queryable within 60 s of upload |
| NFR-4 | **Concurrency** | 20 concurrent users with < 25% p95 latency degradation |
| NFR-5 | **Scale** | 500 pages / 50 MB per document; 200 documents per matter |
| NFR-6 | **Availability** | 99.5% monthly, excluding planned maintenance |
| NFR-7 | **Confidentiality** | Zero-data-retention agreement with the model provider; no training on customer data; TLS 1.3 in transit, AES-256 at rest. The same posture is contractually required of **every** vendor processing document text |
| NFR-8 | **Deletion** | Deletion purges original, extracted text, chunks, embeddings, and provider-side copies within 24 h, verifiably |
| NFR-9 | **Auditability** | Audit log retained 12 months, exportable |
| NFR-10 | **Cost** | ≤ $0.35 for the first question against a 100-page document; ≤ $0.05 per follow-up (§7) |
| NFR-11 | **Portability** | Model provider, embedding provider, vector store swappable with no API or frontend change |
| NFR-12 | **Accessibility** | WCAG 2.1 AA on upload, Ask, and citation-viewing flows |
| NFR-13 | **Observability** | Structured logs, request tracing, per-request token and cost emitted as metrics |

### 6.1 On the absence of authentication

Phase 1 is single-user with no login — a deliberate scope cut, not an oversight, removing sessions, permissions, and tenancy from the MVP. It is defensible only because the **data model is built as though users exist**: every matter and document carries an owner field from day one and all queries filter on it, so Phase 2 authentication becomes a middleware and frontend change rather than a schema migration. NFR-7 through NFR-9 apply in Phase 1 regardless.

The transferable lesson: *a scope cut you can defend in writing is engineering; the same cut undocumented is debt.*

---

## 7. Cost Model

Per Ask query against a 100-page document (~75K input, ~800 output tokens) on `claude-opus-5-5` — $4.00 / $20.00 per million input / output tokens, cached input read at $0.20 per million:

| Path | Input | Input cost | Output cost | **Total** |
|---|---|---|---|---|
| Whole document, first question | 75K | $0.300 | $0.016 | **~$0.32** |
| Whole document, cached follow-up | 75K | $0.015 | $0.016 | **~$0.03** |
| RAG, 8 retrieved chunks | ~7K | $0.028 | $0.016 | **~$0.04** |

The instructive result: **a cached whole document is cheaper per follow-up than uncached retrieval** — and better grounded, since nothing can be missed. The ~20× cache discount, not the retrieval, is what makes repeated questioning affordable. Caching is prefix-based, so stable content (system prompt, document) must precede the volatile question; cache *writes* carry a premium over base input price, to be confirmed against current pricing at implementation.

Cheaper tiers (`claude-sonnet-5-5` at $2/$10, `claude-haiku-4-5` at $1/$5) are candidates for mechanical routes once quality is **measured** per NFR-1, not assumed.

---

## 8. Trust, Privilege, and Professional Responsibility

**8.1 No uncited assertions.** The hard requirement (NFR-1). "The AI made it up" is not a defense that protects a law license.

**8.2 Privilege and confidentiality.** Uploaded documents are privileged attorney–client material — legally protected from disclosure. Hence NFR-7 and NFR-8, and hence the vendor constraint: with no first-party embedding model from the LLM provider, embeddings mean a **second vendor processing privileged text**, requiring its own data-processing agreement. This is the one open compliance thread in the architecture (§9).

**8.3 Unauthorized practice of law.** Non-lawyers giving legal advice is prohibited, as is a tool positioning itself as the adviser. Legal Lens describes what documents say and never advises on what to do; drafts require attorney review and are labeled as such (FR-17).

**8.4 Human in the loop.** FR-19 is absolute across all phases. The output of Draft is text in an editor.

---

## 9. Assumptions, Deferred Decisions, Risks

**Assumptions** — stated, not established; overrule any of them: US jurisdiction, general practice, English only; PDF/DOCX/TXT are sufficient inputs; `.docx` is the export attorneys actually work in; the §6 targets are calibrated to be *defensible* for a class demonstration, not derived from production load data.

**Deferred:** vector store, embedding model and vendor, chunking, prompt design (→ Technical Architecture Document, criteria in §4.3); authentication and multi-user (→ Phase 2); OCR (→ Phase 2, rejected in Phase 1 per FR-2).

**Open thread:** the embedding vendor's data posture (§8.2) — a third-party embedding API with its own DPA, or locally-run open-weight embeddings so no second vendor sees document text. Unresolved.

| Risk | Mitigation |
|---|---|
| A fabricated citation reaches a filing | NFR-1 groundedness bar; native API citations; FR-17 draft labeling; FR-20 audit trail |
| Structured Summary and citations are mutually exclusive in one API call (§4.1) | Two-pass generation — ground the narrative with citations, then extract the 9 fields from the cited output. Resolve and test before Summary is built |
| Naive chunking splits a defined term from its definition, producing confidently wrong retrieval | Clause-aware chunking (§4.3); the §4.2 whole-document path avoids it entirely at Phase 1 sizes |
| Absence questions ("is there no liability cap?") fail under retrieval | §4.2 whole-document path; FR-13 explicit absence reporting |
| A scanned PDF ingests as empty text and the system answers confidently about nothing | FR-2 detect-and-reject; the silent-failure mode is worse than a clear rejection |

---

## 10. Roadmap

| Phase | Scope |
|---|---|
| **1 — MVP** | Single user, no auth. Upload, Ask, Summary, Draft over uploaded documents, with citations |
| **2 — Firm** | Authentication, per-user libraries, matters in the UI, shared documents, OCR |
| **3 — Corpus** | Statutes and case law as a second retrieval scope; authority citations in Draft; citation verification |
| **4 — Agentic** | Multi-agent workflows (research / draft / review). Out of scope now; the architecture must not foreclose it |

---

## 11. Glossary

**Matter** — one case or transaction; the unit documents group into. **Privilege** — legal protection making attorney–client communication non-disclosable. **UPL** — unauthorized practice of law. **Pleadings** — formal court filings defining a dispute. **Discovery** — the evidence-exchange phase; source of the largest document volumes. **MSA** — Master Services Agreement, a long-form commercial contract. **Indemnification** — a contractual promise to cover another party's losses. **Governing law** — which jurisdiction's law interprets the contract. **Legal memorandum** — an internal written analysis; the primary Draft output.
