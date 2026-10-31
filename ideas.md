# Legal Lens — Collected Ideas

> **Status:** Pre-architecture brain dump. This document exists to collect thinking so the **BRD** (functional + non-functional specs) can be written from something solid. It is deliberately light on technical nuance — vector DB, embedding model, chunking strategy and RAG topology are *consciously deferred* to the technical architecture step.
>
> **Last updated:** 2026-09-30

---

## 1. The Pitch

**Legal Lens is an AI assistant for practicing attorneys that answers questions about their own documents, summarizes them, and drafts from them — with every claim traceable to a page.**

### The problem

Legal work is document-bound in a way most knowledge work isn't:

- **Volume.** A single matter can carry hundreds of pages — contracts, pleadings, discovery, correspondence. Finding the one indemnification clause that matters is a manual scan.
- **Repetition.** Summarizing a contract into "who are the parties, what are the obligations, when does it terminate, what's unusual" is the same cognitive task performed over and over on different documents.
- **Blank-page cost.** Memos and case drafts start from scratch or from a half-remembered precedent in someone's folder.
- **Billable reality.** Time spent on mechanical review is time that either gets written off or gets billed in a way clients increasingly push back on.

### Why an AI tool helps here — and why the usual AI tool doesn't

A general chatbot fails at legal work for one specific reason: **it will confidently invent a citation.** This is not a theoretical concern — attorneys have been sanctioned for filing briefs that cited cases which did not exist. Any legal AI product that cannot show its work is unusable by a professional whose license is on the line.

So the differentiating constraint for Legal Lens is not "answer well." It's **answer only from the documents in front of it, and show exactly where each answer came from.**

---

## 2. Who It's For

| | Persona | What they want |
|---|---|---|
| **Primary** | Practicing attorney (solo or small-firm) | Fast, trustworthy answers about their own matter documents; a first draft they can edit rather than a blank page |
| **Secondary** | Paralegal / legal assistant | Summarization and document triage at volume; prep work before the attorney looks at it |
| **Explicitly not** | End clients / the general public | Legal Lens is a tool *for* licensed professionals, not a legal-advice service for consumers. See §8. |

**Phase 1 is a single user with no login.** One attorney, their own documents, on their own machine or a private deployment. Multi-user comes later (see §5).

---

## 3. The Three Features

The product is three verbs: **Ask**, **Summary**, **Draft**. Everything else is plumbing.

### 3.1 Ask

> *"What does this contract say about early termination?"*

**What the user does:** Uploads one or more documents, then types a question in a prompt bar. Can ask follow-ups in a conversation.

**What they get back:** A direct answer, composed only of what the documents actually say, with inline citations pointing to the specific page and passage. If the documents don't answer the question, the answer is *"the documents don't address this"* — not a plausible-sounding guess.

**What "good" looks like:**

- Every factual assertion carries a citation the user can click to see the source passage highlighted.
- Correctly says "not found" rather than extrapolating.
- Handles cross-document questions ("do these two agreements conflict on governing law?").
- Preserves exact quoted language when the wording matters — legal meaning lives in specific words.

**Phase 1 non-goals:**

- No case law, statutes, or regulations. **Uploaded documents only.** (Decision — see §4.)
- No live web lookup.
- No legal conclusions ("you should sue") — descriptions of what documents say, not advice on what to do.

### 3.2 Summary

> *"Summarize this 80-page MSA."*

**What the user does:** Selects an uploaded document and requests a summary.

**What they get back:** Not a paragraph of prose. A **structured** summary, because that's what a lawyer actually reads for:

- Parties and roles
- Effective date, term, renewal mechanics
- Key obligations per party
- Payment / consideration terms
- Termination triggers and notice requirements
- Governing law and dispute resolution / venue
- Liability caps, indemnification, limitation clauses
- **Unusual or off-market provisions** — the "you should look at this" list
- Open/ambiguous items worth a human read

**What "good" looks like:**

- Each field cites its source page.
- Flags absences explicitly (*"No limitation of liability clause found"*) — a missing clause is often more important than a present one.
- Short enough to read in two minutes, with drill-down to the source.
- Consistent shape across documents, so two contracts can be compared side by side.

**Phase 1 non-goals:**

- No multi-document comparison view (though the structured shape makes this a natural Phase 2).
- No risk *scoring* — flag unusual terms, don't pretend to quantify risk numerically.

### 3.3 Draft

> *"Draft a memo on whether the non-compete in this agreement is enforceable."*
> *"Draft a case summary from these pleadings."*

**What the user does:** Describes what they need, selects which uploaded documents are the source material.

**What they get back:** A structured first draft — a legal memo, case summary, or similar — grounded in the selected documents, in a form the attorney edits rather than accepts.

**What "good" looks like:**

- Recognizable professional structure (for a memo: Question Presented / Brief Answer / Facts / Discussion / Conclusion).
- Facts drawn from and cited to the uploaded documents.
- **Visibly marks where attorney input is required** rather than papering over gaps with invented specifics — a placeholder is honest, a fabricated fact is dangerous.
- Clearly labeled as a draft requiring review, in the UI and in the output.
- Exportable to something the attorney can actually work in.

**Phase 1 non-goals:**

- No filing, sending, or any outbound action. Ever. (See §8.)
- No citation to external authority (no case law in Phase 1), so Phase 1 drafts are fact-and-document-driven, not authority-driven. This is a real limitation worth naming: a memo on *enforceability* ultimately wants case law. Phase 1 can draft the structure and the facts; the legal-authority analysis is the attorney's, or a Phase 2 feature.
- No template library in Phase 1.

---

## 4. Decisions Already Made

Recorded so the BRD doesn't relitigate them.

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | **Retrieval scope for Ask** | Uploaded documents only | No corpus licensing, no case-law ingestion pipeline, no citation-to-authority verification problem. "Ask my documents" is a complete, valuable product on its own. A legal corpus is a large Phase 2+ undertaking, not an MVP add-on. |
| D2 | **LLM & data posture** | Cloud model API under a **zero-data-retention** agreement; cloud-hosted vector store | Best answer quality and fastest path to a working product. ZDR plus a no-training commitment is what makes cloud defensible for privileged material. Self-hosting would mean materially worse output and a lot of infra work for a Phase 1. |
| D3 | **Phase 1 users** | Single user, no auth | Removes login, sessions, tenancy, and permissions from the MVP entirely. Gets to a working product faster. Auth and multi-user are deferred, **but the BRD still specifies them** — see D4. |
| D4 | **End goal** | Production-intent, built in phases | This is a real product, not a demo. So the BRD covers the full functional *and* non-functional picture — security, privilege, audit, cost, performance — with Phase 1 explicitly scoped as the MVP slice of it. Writing the NFRs now prevents painting ourselves into a corner that single-user-no-auth would otherwise invite. |
| D5 | **Frontend framework** | **React + TypeScript + Vite** | No preference was expressed, so: React has the deepest ecosystem for the two UI problems this product actually has — streaming chat interfaces and PDF viewing with highlight overlays. TypeScript because the API contract between frontend and FastAPI is worth type-checking. Vite for fast dev iteration. Angular would also work; React is the lower-friction choice here. |

### D3 + D4 tension, acknowledged

"No auth" and "production-intent" pull against each other. The resolution: **the data model and API are designed as if users exist** (every document and matter carries an owner field, even if it's always the same value in Phase 1), while the *login flow* is simply absent. Adding auth later becomes a frontend/middleware change, not a database migration.

---

## 5. Phases

### As originally framed

1. **Frontend** — take inputs: document upload, prompt/search bar, results display
2. **Backend** — Python + FastAPI, package and dependency management with `uv`
3. **AI layer** — RAG

This is a sound **architectural** decomposition and it's how the system should be *structured*. See §11, Suggestion 1 for a thought on whether it should also be the *build order*.

### Roadmap beyond Phase 1

| Phase | Scope |
|---|---|
| **1 — MVP** | Single user, no auth. Upload documents. Ask / Summary / Draft over uploaded documents, with citations. |
| **2 — Multi-user firm** | Login, per-user document libraries, matters as a first-class concept in the UI, shared firm documents |
| **3 — Legal corpus** | Statutes and case law as a second retrieval scope; authority citations in Draft; citation verification |
| **4 — Beyond RAG** | Multi-agent / tool-using workflows: a research agent, a drafting agent, a reviewing agent. Explicitly out of scope now, but the architecture shouldn't foreclose it. |

---

## 6. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| **Frontend** | React + TypeScript + Vite | HTML/CSS as the base; component library TBD. See D5. |
| **Backend** | Python + FastAPI | Async-native, which matters for streaming LLM responses to the UI |
| **Packaging / deps** | `uv` | Single tool for environment + dependency management |
| **AI layer** | RAG | Shape sketched in §7; specifics deferred |
| **Model** | Cloud API under ZDR | See §7.2 for current model/pricing reference |
| **Vector store** | *Deferred* | Technical architecture step |
| **Embeddings** | *Deferred — and needs a vendor decision, see §10* | |
| **Document storage** | *Deferred* | |
| **Metadata DB** | *Deferred* | Postgres is the obvious default |

---

## 7. AI Layer — Sketch Only

### 7.1 Pipeline shape

```
Upload → Extract text → Chunk → Embed → Store in vector DB
                                            ↓
Question → Embed → Retrieve relevant chunks → Assemble prompt → Generate → Answer + citations
```

**Consciously deferred to the technical architecture step:**

- Which vector database
- Which embedding model (and which vendor — see §10)
- Chunking strategy and chunk size — non-trivial for legal documents, where clause and section boundaries carry meaning and a naive 512-token split will cut a definition in half
- Retrieval approach: dense only, hybrid dense + keyword, reranking
- How many chunks to retrieve, and how to handle the context budget
- Prompt structure per feature
- Whether Ask / Summary / Draft share one pipeline or diverge

### 7.2 Model and cost reference

Current as of 2026-09-25. Recorded here because **cost per query is a product constraint**, not just an implementation detail — it sets pricing and it sets how generous the retrieval budget can be.

| Model | ID | Context | Input $/1M | Output $/1M |
|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | 1M | $4.00 | $20.00 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 1M | $2.00 | $10.00 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1.00 | $5.00 |

Opus 5.5 as the default for reasoning-heavy work (Ask, Draft); cheaper tiers are a live option for high-volume or mechanical routes once quality is measured, not assumed. Cached input on Opus 5.5 reads at **$0.20/1M** — a ~20× discount that matters enormously for the repeat-questions-about-one-document pattern (see Suggestion 2).

### 7.3 Citations are a platform feature, not something to build

**This is the most important technical fact in this document.**

The Claude API supports native citations: flag a document as citable (`citations: {enabled: true}` on the document content block) and the response comes back split into text blocks, where each cited block carries a `citations` array. Each citation gives:

- `cited_text` — the exact source passage
- `document_title` and `document_index` — which document
- A precise location: `page_location` with 1-indexed `start_page_number` / `end_page_number` for PDFs, or `char_location` with character offsets for text

So "show me the page this came from," with a clickable highlight in a PDF viewer, is **supported infrastructure** rather than a bespoke citation-matching system to invent. Given that hallucinated citations are the catastrophic failure mode for this product (§1), this materially de-risks the whole thing.

**One important constraint:** citations are **incompatible with structured outputs** (`output_config.format`) — sending both returns a 400. This directly collides with Summary, which wants both structured fields *and* per-field citations. See §9 for the design problem this creates.

### 7.4 Other platform facts worth recording

- **Files API** — a document uploaded once returns a reusable `file_id`, so many questions can be asked against one document without re-uploading it each time. Fits the Ask-with-followups pattern exactly.
- **PDF limits** — 32 MB per request, 600 pages (100 pages on 200K-context models). Real caps that belong in the NFRs as document size limits.
- **Prompt caching** — up to 4 cache breakpoints per request; caching is prefix-based, so stable content (system prompt, document text) goes first and the volatile part (the question) goes last. Any byte change in the prefix invalidates everything after it.
- **ZDR caveat** — Claude Fable 5.1 is *not* available under zero data retention without express authorization. Opus 5.5 is, so D2 is unaffected — but this is recorded so nobody later "upgrades" the model and silently breaks the compliance posture.
- **No first-party embedding model.** The embedding provider will be a separate vendor. This has a real consequence for D2 — see §10.

---

## 8. Trust & Guardrails

The section a generic BRD would skip, and the section this product lives or dies on.

### 8.1 No uncited assertions

**Hard requirement.** Every factual claim in Ask and Summary output must trace to a span in a real uploaded document. The API's citation feature (§7.3) is the mechanism. Output that can't be grounded should say so rather than fill the gap.

Context for why this is non-negotiable: attorneys have faced sanctions for filing briefs citing nonexistent cases. "The AI made it up" is not a defense that protects a license.

### 8.2 Confidentiality and privilege

Uploaded documents are privileged attorney-client material. That implies:

- Zero-data-retention agreement with the model provider; no training on customer data
- Same contractual posture required from *every* vendor that touches document text — including the embedding provider (§10)
- Encryption in transit and at rest
- Explicit, verifiable document deletion
- Clear data-flow documentation — an attorney should be able to answer "where does my client's contract go?"

### 8.3 Unauthorized practice of law

Legal Lens is a tool for a licensed attorney, not a legal-advice service. Practically:

- Output describes what documents say; it doesn't advise on what to do
- Drafts are first drafts requiring attorney review, labeled as such in the UI and in the output itself
- No end-client-facing mode in any planned phase

### 8.4 Human in the loop on Draft

No automated filing, sending, serving, or any outbound action — in any phase. The output of Draft is text in an editor, full stop.

### 8.5 Auditability

For every answer, retain: who asked, what they asked, which documents and which spans grounded the response, which model and version produced it, when. Needed for debugging, for cost attribution, and — in a worst case — for malpractice defense.

---

## 9. Known Design Problems

Things already identifiable as genuinely hard, worth naming before the BRD so they get budgeted for.

1. **Structured Summary vs. citations.** Citations and structured JSON output are mutually exclusive in one API call (§7.3). Summary wants both. Likely resolutions: two passes (extract structure, then ground each field), or citations-on plus a prompt-enforced output shape, or structure derived client-side from a cited narrative. Needs a decision and a test.
2. **Chunking legal documents.** Clause boundaries, defined terms, cross-references ("as defined in Section 2.1") and nested numbering all mean that naive fixed-size chunking will produce retrieval that technically works and substantively misleads.
3. **"Not found" is a feature.** Getting a model to reliably decline rather than extrapolate takes real prompt work and real evaluation. It is also the single behavior most responsible for whether an attorney trusts the tool.
4. **Absence detection in Summary.** *"There is no limitation of liability clause"* requires reasoning about what *isn't* in the document — which retrieval, by construction, is bad at. Retrieval returns what matches; it can't return what's missing.
5. **Scanned documents.** A meaningful share of real legal PDFs are images. Without OCR, extraction silently returns nothing useful. See Suggestion 5.

---

## 10. Open Questions

To resolve before or during the BRD.

**Product scope**
- [ ] Jurisdiction focus? (US federal + state? A specific state? Non-US?) Affects terminology, document conventions, and any Phase 3 corpus
- [ ] Practice area focus, or general? (A contracts-focused tool can be much sharper than a general one)
- [ ] Language(s) — English only in Phase 1?
- [ ] Is Summary output editable, or read-only?
- [ ] Export formats for Draft — `.docx`? PDF? Plain Markdown? (`.docx` is what attorneys actually work in)

**Documents**
- [ ] Accepted formats — PDF, DOCX, TXT? Email formats?
- [ ] Max document size and page count (platform caps in §7.4 set the ceiling, but the product limit may be lower)
- [ ] **Scanned/image PDFs — accept with OCR, or reject with a clear error?**
- [ ] Expected document volume per matter? (10 documents vs. 10,000 changes the architecture completely)
- [ ] Retention and deletion policy — how long are documents kept, who can delete, is deletion verifiable?
- [ ] What happens to documents when a matter closes?

**Commercial / operational**
- [ ] Target cost ceiling per query, and per matter per month? (Sets the retrieval budget and the model tier)
- [ ] Pricing model — per seat, per matter, usage-based?
- [ ] Deployment target — SaaS, private cloud per firm, on-prem option?

**Vendor / compliance**
- [ ] **Embedding provider** — there's no first-party Anthropic embedding model, so this is a *second* vendor seeing privileged document text, requiring its own DPA and retention commitments. Options span third-party API embeddings (simplest, another vendor in scope) and locally-run open-weight embeddings (no third party sees text, more infra). This is the one place where D2's posture has a loose end.
- [ ] Vector DB hosting — same question, third vendor
- [ ] Any certification requirements firms will ask for (SOC 2)?

---

## 11. Suggestions to Consider

Raised for consideration, not decided. Accept, reject, or defer each.

### Suggestion 1 — Build a vertical slice before building layer by layer

**The concern:** Building the complete frontend first (against mocked data), then the complete backend, then the AI layer means all integration risk lands at the end — and the riskiest question in the whole project (*does retrieval actually surface the right clause?*) gets answered last, after the UI is already built around assumptions about what the AI returns.

**The suggestion:** Keep Frontend / Backend / AI as the *architecture*, but build the first increment as a thin vertical slice through all three: **upload one PDF → ask one question → get one cited answer.** Ugly is fine. Then broaden feature by feature, with each layer already proven to talk to the next.

**Why it's worth considering here specifically:** the UI for Ask is substantially shaped by the citation format the AI layer returns. Designing the citation UI before knowing the citation data structure means rework.

### Suggestion 2 — A 1M-token context window changes the RAG calculus

**The observation:** An 80-page contract is roughly 60–90K tokens. The context window is 1M. The document fits whole, with room to spare. And with prompt caching, follow-up questions against that same cached document read at **$0.20/1M instead of $4.00/1M** — plus native citations give exact page numbers without any retrieval machinery at all.

**What this means:** For single-document Q&A — which is exactly what Phase 1 is scoped to (D1) — RAG may be solving a problem that doesn't exist yet. Retrieval earns its place when the corpus genuinely exceeds the context window: large discovery sets, matter-wide search across hundreds of documents, cost control at volume.

**The suggestion:** Not "drop RAG." Rather, make retrieval strategy a **documented threshold decision**:

| Corpus size | Strategy |
|---|---|
| Fits comfortably in context | Whole document(s) in context + prompt caching + native citations |
| Exceeds context, or cost-sensitive at volume | RAG retrieval |

This keeps RAG as the scaling path (and Phase 2+ absolutely needs it) while potentially making Phase 1 radically simpler and *more* accurate — no chunking artifacts, no retrieval misses, and §9.4 (absence detection) becomes tractable because the model sees the whole document.

### Suggestion 3 — Make Summary structured from the start

Already reflected in §3.2, flagged here because it's a change from "summarize a legal document." Prose summaries read nicely and are hard to act on. Fielded summaries are comparable across documents, skimmable, and each field can carry its own citation. Note the API tension in §9.1.

### Suggestion 4 — Add a "matter" concept to the data model now

Legal documents clump by case/matter, not into one flat pile. Even though Phase 1 is single-user with a simple document list, the schema should have `matter_id` from day one. Retrofitting grouping onto flat documents later means a migration plus reworking every query and every retrieval filter. Cheap now; expensive in Phase 2.

### Suggestion 5 — Decide the OCR question early

A large share of real-world legal PDFs are scans. Text extraction on a scanned PDF returns nothing, and without a check the product will accept the document and then confidently answer questions about an empty string. Minimum viable handling: **detect a text-less PDF and fail loudly.** Better: OCR it. Either way, decide before Phase 1 ships, because the silent-failure version is worse than a clear rejection.

### Suggestion 6 — Define evaluation before building

"Is it answering correctly?" needs to be a measurement, not a vibe. Build a small held-out set: ~30–50 triples of *(document, question, expected answer + expected citation)*, drawn from real document types. Include negative cases — questions the documents genuinely don't answer — because "correctly says I don't know" is a top-tier requirement (§9.3) and the only way to know if prompt changes break it is to test it.

Cheap to build now. Very expensive to retrofit once prompts are being tuned by feel and nobody can tell whether yesterday's change helped.

### Suggestion 7 — Reconsider the folder name

`Legal Lens` has a space in it. Fine for a docs folder; mild friction later for `uv`, npm scripts, Docker paths and CI. If this folder becomes the repo root, `legal-lens` would save some quoting. Trivial, but trivial to fix now and annoying to fix later.

---

## 12. Proposed BRD Outline

The next deliverable. Suggested structure:

1. **Executive Summary** — what Legal Lens is, who it's for, why now
2. **Business Objectives & Success Metrics** — what success looks like, measurably
3. **Scope** — in scope for Phase 1, out of scope, future phases
4. **Stakeholders & Personas**
5. **Functional Requirements**
   - 5.1 Document management (upload, list, view, delete)
   - 5.2 Ask
   - 5.3 Summary
   - 5.4 Draft
   - 5.5 Citation & source display
   - 5.6 Export
6. **Non-Functional Requirements**
   - 6.1 Accuracy & groundedness — *including the no-uncited-assertions requirement*
   - 6.2 Performance & latency — *first token, full answer, ingestion time*
   - 6.3 Security, confidentiality & privilege
   - 6.4 Data retention & deletion
   - 6.5 Auditability & logging
   - 6.6 Cost per query / per matter
   - 6.7 Scalability — document count, concurrent users
   - 6.8 Availability
   - 6.9 Usability & accessibility
7. **Compliance & Professional Responsibility** — privilege, UPL posture, vendor DPAs
8. **Assumptions & Constraints**
9. **Risks & Mitigations** — hallucinated citations front and center
10. **Dependencies** — model provider, embedding provider, vector DB, OCR
11. **Phased Delivery Plan**
12. **Open Questions** — carried forward from §10
13. **Glossary** — legal and technical terms, since this document spans both audiences

**Deliberately not in the BRD:** vector DB selection, embedding model, chunking strategy, retrieval topology, prompt engineering. Those belong in the Technical Architecture Document that follows.
