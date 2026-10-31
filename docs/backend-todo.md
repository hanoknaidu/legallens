# Legal Lens — Backend Implementation To-Do

> **Scope of this phase: the backend only.** FastAPI, SQLite, ingestion, the full API surface
> from the architecture diagram. **No orchestration layer and no live model calls** — those are
> seams (§8), not work items here. The frontend is not touched except for the two-line
> integration diff in §14.
>
> The deliverable is a service that boots with **no API key, no Qdrant, no frontend and no
> orchestrator**, and answers every endpoint in §5 with real data derived from really-parsed
> uploaded files.
>
> v0.1 · 2026-10-05 · approve or amend before implementation starts

---

## 1. Ground Rules

1. **Standalone and end-to-end.** `uvicorn legal_lens.main:app` must be enough. Upload a real
   PDF, get real pages, ask a real question, receive a streamed answer with citations that point
   at pages that genuinely contain the quoted text. Nothing stubbed at the network boundary,
   nothing requiring a service that isn't in `pyproject.toml`'s base dependencies.
2. **The contract is frozen.** `src/legal_lens/schemas.py` already mirrors
   [frontend/src/types/domain.ts](../frontend/src/types/domain.ts). Backend is snake_case, the wire
   is camelCase, `Wire` handles the translation. No field is renamed, so no frontend type changes.
3. **The orchestration layer is a seam, not a stub.** The intent router, the three workflows and
   the ten-step AI pipeline in the diagram sit behind two interfaces — `IntentRouter` and
   `GenerationEngine`. Phase 1 ships a deterministic local implementation of each (§8). Swapping
   in Claude and the real workflows changes no route, no schema, no SQL.
4. **Owner on every row, owner in every query** (BRD §6.1). `PHASE1_OWNER_ID` is resolved in one
   dependency; repositories take `owner_id` as a required argument. Phase 2 auth replaces the
   dependency and nothing else.
5. **Nothing fails silently.** FR-2 rejection carries its reason; abstention is a 200 with
   `abstained: true`, never an error; a document that is not `ready` cannot be scoped into a
   question (409, with which document and why).
6. **Nothing blocks the event loop.** PDF parsing, DOCX parsing and chunking run in a worker
   thread. The only things awaited on the loop are I/O.
7. **Every generation is audited** (FR-20) and every model call is costed (NFR-10), including the
   deterministic one — a cost of `$0.00` is still a row, and the audit path has to be exercised
   before the real models make it expensive to discover it was wrong.

---

## 2. Starting Point — What Already Exists

| File | State | Note |
|---|---|---|
| `pyproject.toml` | ✅ done | Base deps + `qdrant` / `docling` / `embeddings-local` / `dev` extras. `.venv` is provisioned (FastAPI 0.142, pypdf 6.19, python-docx 1.2, anthropic 1.11, pytest 9.1, httpx 0.28) |
| `config.py` | ✅ done | Every provider is a setting. Thresholds, limits, model ids, `PHASE1_OWNER_ID` |
| `schemas.py` | ✅ done | The wire contract. Needs the five additions in §5.9 |
| `db/schema.sql` | ✅ done | 12 tables, owner-scoped, cascade-deleting, `deletion_jobs` for what SQLite can't reach |
| `db/session.py` | ✅ done | Single serialised connection, WAL, `execute_script` for all-or-nothing writes |
| `observability.py` | ✅ done | JSON logs with request id, in-process `METRICS` sink |
| `tests/` | empty | §12 |
| everything else | **not built** | no `main.py`, no routers, no repositories, no ingestion, no engines |

So this phase writes the middle: app assembly, routes, repositories, ingestion, the generation
seam, exports, and tests.

---

## 3. Decisions Made — Overrule Any of These

| Area | Choice | Why |
|---|---|---|
| Server | uvicorn, **single worker** | SSE streams and the pending-turn registry are in-process, and SQLite takes one writer. Multi-worker needs Redis and buys nothing at NFR-4's 20 users. Documented in the README as a deployment constraint, not left to be discovered |
| Database | SQLite via aiosqlite | Diagram §5 specifies it for Phase 1. All SQL lives in `db/repositories/`, so Postgres is a driver swap, not a rewrite |
| ORM | none | The schema is 12 tables of flat rows read in known shapes. An ORM would add a layer whose main contribution would be hiding the owner filter that BRD §6.1 makes load-bearing |
| Generation | `GenerationEngine` protocol; Phase 1 = `DeterministicEngine` (`llm_provider="echo"`) | §8. Real grounding, real citations, real abstention, zero network |
| Intent | keyword `IntentRouter`, mirroring the mock's regexes exactly | The Haiku router is orchestration. Mirroring the regexes means the UI behaves identically after the switch |
| Ingestion | in-process `asyncio` background task + `ingestion_jobs` row | NFR-3 is 60 s for 100 pages; a thread and a status row clear it. Celery/Redis would be a second runtime for a job that takes four seconds |
| Parser | `builtin` (pypdf / python-docx / txt); Docling behind the same interface | Docling is a 2 GB optional extra. The interface is what matters now |
| Vector store | `memory` (dict + cosine) implementing the same protocol as Qdrant | Makes `chunksIndexed` a real number and exercises the seam offline |
| Embeddings | `hashing` — deterministic, local, **not semantic** | BRD §8.2: a real embedding vendor is a second processor of privileged text and needs its own DPA first. Shipping a local stand-in keeps that decision open instead of pre-empting it |
| Page viewing | `GET /documents/{id}/file` (original bytes) **+** `GET /documents/{id}/page/{n}` (page text JSON) | The diagram says "page render", but the frontend renders with pdf.js client-side, which needs the file, not server-made images. Rasterising would add a native dependency to produce something nobody consumes |
| Upload transport | `multipart/form-data` | The mock posts JSON metadata because it has no file. This is the one real frontend change (§14) |
| Errors | RFC 9457 `application/problem+json` + `X-Request-Id` on every response | A user-visible failure traces to a log line traces to an audit row |
| Rate limit | in-process token bucket per owner, `rate_limit_per_minute` | Diagram §3 "request validator". One process, one bucket |
| Tests | pytest + `httpx.ASGITransport`, temp SQLite per test, no network | §12 |

---

## 4. Module Layout

```
backend/
├── pyproject.toml ✅ · .env.example · README.md
└── src/legal_lens/
    ├── main.py                app factory · lifespan · CORS · middleware · router mount
    ├── config.py ✅ · schemas.py ✅ · observability.py ✅
    ├── errors.py              ApiProblem + exception handlers → problem+json
    ├── middleware.py          request id · access log · timing · rate limit
    ├── deps.py                get_db · get_services · current_owner_id (Phase 2 seam)
    ├── services.py            container: repositories + engines + ingestion, built at lifespan
    ├── api/
    │   ├── conversations.py   POST/GET/PATCH/DELETE /conversations · GET …/messages
    │   ├── chat.py            POST /chat/message · GET /chat/stream · POST /chat/stop
    │   │                      GET /chat/{msgId}/citations
    │   ├── documents.py       POST/GET/DELETE /documents · /{id}/status · /{id}/file · /{id}/page/{n}
    │   ├── matters.py         GET /matters
    │   ├── exports.py         POST /messages/{id}/export
    │   └── platform.py        GET /health · /metrics · /audit · /audit/export
    ├── db/
    │   ├── schema.sql ✅ · session.py ✅
    │   └── repositories/      matters · documents · conversations · messages · audit · deletion
    ├── ingestion/
    │   ├── pipeline.py        orchestrates the 7 stages, writes ingestion_jobs
    │   ├── upload.py          type/size/page validation → object store
    │   ├── probe.py           text-layer detection (FR-2 gate)
    │   ├── parsers/           base.py · pdf.py · docx.py · txt.py · docling.py (extra)
    │   ├── chunker.py         clause-aware · defined-term carry-forward · parent/child
    │   └── indexer.py         embed + upsert dense, BM25 term weights sparse
    ├── ai/
    │   ├── engine.py          GenerationEngine protocol · GenerationRequest/Chunk/Result
    │   ├── deterministic.py   the Phase 1 engine (§8.2)
    │   ├── anthropic_engine.py  seam: constructed, raises on use unless key present (§8.3)
    │   ├── intent.py          IntentRouter protocol + keyword implementation
    │   ├── retrieval.py       whole-document ⟷ RAG threshold (BRD §4.2) · lexical + dense fusion
    │   ├── embeddings.py      protocol + hashing · local · voyage factory
    │   ├── vectorstore/       base.py · memory.py · qdrant.py (extra)
    │   ├── summary_fields.py  the 9 BRD fields: matchers, labels, order
    │   └── grounding.py       "is every assertion cited?" gate (NFR-1)
    ├── streaming.py           SSE frame encoding · pending-turn registry · cancellation
    ├── export/                docx.py (python-docx) · markdown.py
    ├── audit.py               one writer, called by every generating route
    ├── cost.py                token → USD, price table from BRD §7
    └── objects.py             object store: put/get/delete, checksum, local-FS backend
```

---

## 5. The API Contract

Base path `/api`. All bodies camelCase. Timestamps ISO-8601 UTC with `Z`.

### 5.1 Conversations

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/conversations` | `{title?, matterId?}` | `201` `Conversation` |
| GET | `/conversations` | — | `200` `Conversation[]`, `updatedAt` desc |
| GET | `/conversations/{id}/messages` | — | `200` `Message[]`, `sequence` asc |
| PATCH | `/conversations/{id}` | `{title}` | `200` `Conversation` · `404` |
| DELETE | `/conversations/{id}` | — | `204` · `404` |

`matterId` omitted → the default matter. Title omitted → `"New conversation"`, replaced by the
first user turn truncated to 52 chars + ellipsis (mirrors the mock, so the sidebar behaves
identically). Deleting cascades to messages and citations.

### 5.2 Chat

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/chat/message` | `{conversationId, content, scopedDocumentIds?}` | `200` `SendMessageResponse` · `404` conversation · `409` scoped doc not ready · `422` |
| GET | `/chat/stream?messageId=` | — | `200 text/event-stream` (§6) · `404` unknown/expired turn |
| POST | `/chat/stop` | `{messageId?}` | `204` |
| GET | `/chat/{msgId}/citations` | — | `200` `CitationsResponse` · `404` |

`POST /chat/message` persists the user turn, classifies intent, resolves document scope, registers
a **pending assistant turn** (id returned, nothing persisted yet) and returns. Generation happens
on the stream. The assistant turn is persisted with its citations in one transaction when the
stream completes — a message whose citations failed to write would render as an uncited assertion,
which NFR-1 forbids.

Pending turns expire after 120 s. `POST /chat/stop` with no body cancels whatever this owner has
in flight (Phase 1 is single-user); a partial answer is persisted as-is with audit
`outcome: "stopped"`.

Scope resolution: explicit `scopedDocumentIds` → those; empty → every `ready` document in the
conversation's matter. A requested document that is `queued`/`processing`/`failed` is a 409 naming
it, not a silently dropped source.

### 5.3 Documents

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/documents` | `multipart`: `file`, `matterId?`, `title?` | `201` `LegalDocument` (status `queued`) · `413` too large · `415` wrong type · `422` |
| GET | `/documents?matterId=` | — | `200` `LegalDocument[]`, newest first |
| GET | `/documents/{id}/status` | — | `200` `DocumentStatus` · `404` |
| GET | `/documents/{id}/file` | — | `200` original bytes, `Content-Disposition: inline`, `Accept-Ranges: bytes` · `404` |
| GET | `/documents/{id}/page/{n}` | — | `200` `DocumentPage` · `404` · `416` page out of range |
| DELETE | `/documents/{id}` | — | `204` · `404` |

A **scanned PDF is rejected, not failed-and-forgotten** (FR-2): the row persists with
`status: "failed"` and the verbatim reason the frontend already renders, because a user told only
"failed" re-uploads the same file. Reason text lives in one constant, shared with the mock's
wording.

`DELETE` purges original, pages, chunks, embeddings and citations, then writes one
`deletion_jobs` row per external target (object store, vector store) and reconciles them. An
unresolved row is the evidence that NFR-8 is not yet satisfied — `GET /health` reports the count.

### 5.4 Matters

| Method | Path | Response |
|---|---|---|
| GET | `/matters` | `200` `Matter[]` |

FR-4: on first boot, seed one matter. **Its id is `m-001`** — the frontend falls back to that
literal in [Sidebar.tsx:18](../frontend/src/components/shell/Sidebar.tsx#L18),
[NewChatPage.tsx:23](../frontend/src/pages/NewChatPage.tsx#L23) and
[DocumentsPage.tsx:85](../frontend/src/pages/DocumentsPage.tsx#L85), and matching it keeps §14's
promise that integration is a base-URL change.

### 5.5 Export (FR-18)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/messages/{id}/export` | `{format: "docx" \| "markdown"}` | `200` file bytes + `Content-Disposition: attachment` · `404` · `409` message has no draft/summary |

Both formats carry the FR-17 unreviewed-draft notice **inside the file**, not only in the UI, and
preserve `[ATTORNEY INPUT REQUIRED]` verbatim.

### 5.6 Audit (FR-20, NFR-9)

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/audit` | `?limit=50&offset=0&feature=&conversationId=&since=&until=` | `200` `{entries: AuditEntry[], total}` |
| GET | `/audit/export` | `?format=jsonl` | `200` JSONL stream, `attachment` |

### 5.7 Platform

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `200` `HealthResponse` (`ok`/`degraded`; checks: db, object store writable, llm provider, vector store, pending deletions) |
| GET | `/metrics` | `200` `METRICS.snapshot()` — counters + p50/p95/max per route, tokens, cost |

`degraded` never means 503: a missing API key degrades generation, it does not take the service
down, and the frontend must be able to tell the difference.

### 5.8 Errors

```json
{ "type": "about:blank", "title": "Document is not ready",
  "status": 409, "detail": "d-7f3a1c is still processing.",
  "requestId": "req-4b81…", "code": "document_not_ready" }
```

`code` is the stable machine-readable string; `detail` is user-presentable. 422 keeps FastAPI's
validation payload under `errors`.

### 5.9 Additions to `schemas.py`

Five models, additive, none renaming an existing field:

| Model | Purpose |
|---|---|
| `DocumentPage` | `{documentId, page, text, charCount}` for §5.3 |
| `ProblemDetail` | §5.8 |
| `AuditPage` | `{entries, total}` |
| `MetricsResponse` | typed `METRICS.snapshot()` |
| `StreamMeta` / `StreamToken` / `StreamDone` | the SSE frames in §6, so the protocol is in the contract file and not only in `streaming.py` |

---

## 6. The SSE Protocol

Must match [frontend/src/lib/sse.ts](../frontend/src/lib/sse.ts) byte for byte: frames separated by a
blank line, `event:` then `data:` lines, JSON payloads.

```
event: meta
data: {"id":"a-9f21","intent":"ask","grounded":true,"abstained":false,
       "citations":[{"n":1,"documentId":"d-msa","documentTitle":"Master Services Agreement",
                    "page":14,"citedText":"Either party may terminate…"}],
       "summary":null,"draft":null}

event: token
data: {"text":"Either "}

event: citations          ← see below
data: {"citations":[…]}

event: done
data: {"id":"a-9f21","grounded":true,"abstained":false}
```

- **`meta` first, carrying citations when they are already known.** With the deterministic engine
  they always are — grounding is resolved before composition — and the same holds for the
  two-pass summary and draft paths. This is what lets the current frontend work unchanged.
- **`citations` is specified now and emitted but ignored today.** When the real Claude stream
  lands, citation blocks arrive interleaved with text and `meta` can no longer carry them. The
  event exists so that day is a frontend addition rather than a protocol break. Flagged in §15 —
  it is the one place where the "base-URL change only" promise has a known expiry.
- `done` repeats the final flags, so a consumer that acts on `meta` optimistically can correct.
- `error` frames carry a `ProblemDetail`. Client disconnect cancels the generation task; it does
  not leave an orphan writing to the database.
- Headers: `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. A
  `: keep-alive` comment every 15 s so no proxy closes a slow first token.

---

## 7. Ingestion Pipeline (diagram §6)

Triggered by `POST /documents`, run as a background task, each stage writing an `ingestion_jobs`
row so a failure says *which stage*.

| # | Stage | Rules |
|---|---|---|
| 1 | Upload handler | `.pdf`/`.docx`/`.txt` by extension **and** sniffed magic bytes; ≤ 50 MB (FR-1); SHA-256 checksum; stream to object store without buffering the whole file |
| 2 | Text-layer probe | Per page, extractable chars < `min_chars_per_text_page` (24) for **every** page → FR-2 rejection. Some pages empty is normal (exhibit dividers); all pages empty is a scan |
| 3 | OCR | **Phase 2.** Not built. The rejection in stage 2 names it as unavailable |
| 4 | Parser | pypdf per page → `document_pages`; DOCX paragraphs + tables, page-broken on explicit breaks else ~3 000 chars; TXT form-feed or size. > 500 pages → fail with the limit named (NFR-5) |
| 5 | Clause-aware chunker | Split on clause/section headings (`^\s*(\d+(\.\d+)*)\s+[A-Z]`, `ARTICLE [IVX]+`, `SECTION \d+`) before falling back to `chunk_target_chars` with overlap. Never split mid-sentence. Carry the defined terms a chunk uses into `defined_terms` — a chunk severed from a definition produces confidently wrong retrieval. Parent id set for parent-expansion at retrieval |
| 6 | Sparse indexer | BM25 term weights, legal-term boosting; written to the vector store's sparse side |
| 7 | Embedder | Batch, retry with backoff, upsert dense + sparse with payload `{ownerId, matterId, documentId, page, section}`; `chunk_metadata.indexed = 1` as batches land, so `chunksIndexed` is a true count |

Status transitions: `queued` → `processing` → `ready` \| `failed`. Also enforced:
`max_documents_per_matter` (200, NFR-5) and a `pages`/`tokenEstimate` write-back used by the
§4.2 threshold.

---

## 8. The Generation Seam

### 8.1 The interface

```python
class GenerationEngine(Protocol):
    async def generate(self, req: GenerationRequest) -> AsyncIterator[GenerationEvent]: ...
```

`GenerationRequest` carries intent, question, conversation history, resolved document scope,
retrieval path and token budget. `GenerationEvent` is `Meta | Token | Citations | Usage | Done`.
Everything above this interface — routes, SSE, persistence, audit, cost — is written once and does
not change when the implementation does.

### 8.2 `DeterministicEngine` — the Phase 1 implementation

Not a mock returning canned text. It answers from the real extracted pages, which is what makes
the backend end-to-end and every BRD state reachable without a model:

- **Retrieval path** is chosen by the real BRD §4.2 threshold against `tokenEstimate`:
  whole-document below `whole_document_token_budget`, chunk retrieval above. Recorded as
  `retrieval_path` in the audit row either way, so the decision is observable from day one.
- **Ask** — score pages/chunks lexically against the question (BM25-ish, legal-term boosted), take
  the best spans, compose a templated answer that *quotes* them with `{{cite:n}}` markers and
  returns matching `Citation` objects whose `page` is the page the text actually came from. Below
  `rerank_min_score` → **abstain**: `abstained: true`, `grounded: true`, no substitute answer
  (FR-9). The abstention path is real, not a fixture.
- **Summary** — the nine BRD fields from `summary_fields.py`, each with a clause matcher over the
  extracted text. Found → quoted value + citations. Not found → `value: null`, which is FR-13's
  explicit absence report, not an omission. Always nine fields, always in `SUMMARY_FIELDS` order.
- **Draft** — Question Presented / Brief Answer / Facts / Discussion / Conclusion, facts cited to
  sources, `[ATTORNEY INPUT REQUIRED]` wherever a fact is absent — never a plausible invention
  (FR-16). FR-17 banner in the payload and in both export formats.
- **Streaming** — word chunks on an uneven cadence, so the frontend's cursor, stop button and
  no-layout-shift guarantees are tested against something real.
- Every answer passes `grounding.py` before it is sent. An engine that emitted an uncited
  assertion would fail its own gate — which is the point: the gate is tested before the model
  that needs it exists.

### 8.3 Where Claude plugs in

`anthropic_engine.py` is written as the interface plus the request-assembly shape (stable cache
prefix first, documents as citable blocks, per-intent output contract) and raises a clear
`ApiProblem` if selected without a key. Its body — the actual streamed call — is **the next
phase's work, together with the orchestrator**. Deliberately empty here, so the boundary is
visible rather than half-crossed.

---

## 9. Data Layer Rules

1. One repository per table group; **all** SQL lives there. No SQL in a route.
2. Every read and write takes `owner_id`. A repository method without it is a bug, not a
   convenience.
3. Writes that must not partially land — assistant turn + citations, document + ingestion job —
   go through `execute_script`.
4. Deletion: SQLite cascade for everything reachable, `deletion_jobs` for everything not. Verified
   by a test that deletes a document and asserts zero rows across all five derived tables plus a
   resolved job row per external target (NFR-8).
5. `datetime` in, ISO-8601 text out. One `utcnow()` helper; no `datetime.now()` anywhere.

---

## 10. Cross-Cutting

| Concern | Implementation |
|---|---|
| Request id | Middleware mints `req-…`, binds `request_id_var`, returns `X-Request-Id`. Every log line and problem body carries it |
| Access log | One JSON line per request: method, path, status, duration, owner |
| Metrics | `METRICS.observe("http.latency", …, route=…)` per request; tokens and cost per generation (NFR-13) |
| Rate limit | Token bucket per owner, 60/min, `429` + `Retry-After` |
| CORS | `cors_origins` from config, credentials off (Phase 1 has no session to protect) |
| Audit | `audit.py` is the only writer. Called by chat and export with feature, documents, grounding spans, model id, tokens, latency, outcome |
| Cost | `cost.py` holds the BRD §7 price table (opus-5-5 $4/$20 per M, cache read $0.20; sonnet-5-5 $2/$10; haiku-4-5 $1/$5) and writes a `usage_costs` row **per model call**, because NFR-10 is a budget per *question* and a question can make several calls |
| Config | `.env.example` with every `LEGAL_LENS_*` var and its default |

---

## 11. Build Phases

### Phase 0 — App assembly
- [ ] `main.py`: app factory, lifespan (connect db, build services, seed default matter, start reconciler), CORS, router mount under `/api`
- [ ] `errors.py` + handlers → problem+json; `middleware.py` (request id, access log, timing, rate limit)
- [ ] `deps.py`, `services.py`; `GET /health` and `GET /metrics` answering
- [ ] `.env.example`, README with the single-worker constraint and run commands
- [ ] `python -m legal_lens` dev entrypoint; service boots clean with no API key

### Phase 1 — Data layer
- [ ] `objects.py` local-FS object store with checksum
- [ ] Repositories: matters, documents, conversations, messages, audit, deletion — every method owner-scoped
- [ ] `utcnow()` / row→model mapping helpers
- [ ] Default matter `m-001` seeded idempotently on boot (FR-4)

### Phase 2 — Documents + ingestion  ← *the long pole*
- [ ] `POST /documents` multipart with type/size validation (FR-1); 413/415 with named limits
- [ ] Parsers: pdf (pypdf), docx (python-docx), txt; page-granular `document_pages`
- [ ] Text-layer probe and the FR-2 rejection, reason text shared with the frontend wording
- [ ] Clause-aware chunker with defined-term carry-forward and parent ids
- [ ] Embeddings (hashing) + memory vector store behind their protocols; `chunksIndexed` real
- [ ] Background pipeline with `ingestion_jobs` per stage; `queued → processing → ready/failed`
- [ ] `GET /documents`, `/{id}/status`, `/{id}/file` (ranges), `/{id}/page/{n}`
- [ ] `DELETE /documents/{id}` with cascade + `deletion_jobs` + reconciler (NFR-8)
- [ ] 100-page PDF queryable within 60 s, measured (NFR-3)

### Phase 3 — Conversations
- [ ] All five conversation endpoints; auto-title from the first user turn
- [ ] Message history with citations hydrated, `sequence` ordering
- [ ] Cascade delete verified

### Phase 4 — Chat + streaming  ← *the core*
- [ ] `IntentRouter` keyword implementation, mirroring the mock regexes
- [ ] Scope resolution + the 409 for a not-ready document
- [ ] `streaming.py`: pending-turn registry, SSE encoder, keep-alive, cancellation on disconnect
- [ ] `POST /chat/message` → `GET /chat/stream` → persisted turn + citations in one transaction
- [ ] `POST /chat/stop`: cancel, persist partial, audit `outcome: "stopped"`
- [ ] `GET /chat/{msgId}/citations`
- [ ] Audit row + `usage_costs` row per generation

### Phase 5 — The deterministic engine
- [ ] `GenerationEngine` protocol and event types; `retrieval.py` with the §4.2 threshold
- [ ] Ask: lexical grounding, real citations, real page numbers
- [ ] Abstention below threshold (FR-9) — no substitute answer
- [ ] Summary: nine fields, absent → `null` (FR-13)
- [ ] Draft: memo structure, `[ATTORNEY INPUT REQUIRED]` (FR-16), FR-17 banner
- [ ] `grounding.py` gate applied to every answer before it leaves (NFR-1)
- [ ] `anthropic_engine.py` seam with request assembly and a clear unimplemented error

### Phase 6 — Export + audit
- [ ] `POST /messages/{id}/export` → .docx (python-docx) and Markdown, FR-17 notice inside the file
- [ ] `GET /audit` with filters and paging; `GET /audit/export` JSONL
- [ ] Retention sweep honouring `audit_retention_days` (NFR-9)

### Phase 7 — Tests
- [ ] §12 in full, green

### Phase 8 — Hardening
- [ ] `ruff check` and `ruff format` clean; type hints on every public function
- [ ] Concurrency smoke: 20 parallel asks, p95 degradation < 25% (NFR-4)
- [ ] 50 MB upload and 500-page document behave as specified, not as a 500
- [ ] No unawaited task, no connection leak, no blocking call on the loop (verified by asyncio debug mode)
- [ ] README: run, test, configure, swap a provider, the Phase 2 auth seam

---

## 12. Testing Plan

`pytest`, `httpx.ASGITransport`, a temp SQLite and temp object store per test, no network.

| Area | Tests |
|---|---|
| Contract | Every response validates against `schemas.py` **and** serialises camelCase. One test asserts the JSON keys of each response match the frontend's `domain.ts` field names |
| Documents | Upload each type; reject oversize, wrong type, > 500 pages; **scanned-PDF rejection carries its reason**; status transitions; page text matches the source; delete purges all five derived tables and resolves its jobs |
| Conversations | CRUD, auto-title, ordering, cascade, 404s |
| Chat | Message → stream → persisted turn; SSE frame shape parsed by a reimplementation of the frontend's parser; stop mid-stream persists a partial; 409 on a not-ready scope; citations endpoint matches the stream's |
| Engine | Ask returns ≥ 1 citation whose `citedText` is **actually present** on the cited page — the test that makes a fabricated citation impossible to ship; unanswerable question abstains; summary returns exactly 9 fields with absences as `null`; draft contains the attorney-input marker when a fact is missing |
| Grounding | NFR-1 gate rejects a synthesised uncited assertion |
| Audit | One row per generation with every FR-20 field populated; `usage_costs` row per call |
| Owner scoping | A repository query with a different `owner_id` returns nothing — the Phase 2 regression test, written now |
| Platform | Health degrades without a key but stays 200; metrics count requests and latencies |

Fixtures: a small generated native-text PDF, a DOCX, a TXT, and a **text-layer-free PDF** built at
test time so the FR-2 path needs no binary in the repo.

---

## 13. Definition of Done

1. `uvicorn legal_lens.main:app` boots with no API key, no Qdrant, no orchestrator, no frontend.
2. Upload a real 40-page MSA → it reaches `ready` → ask a question → a streamed, cited answer
   whose quoted text is verifiably on the cited page.
3. Ask something the documents don't answer → abstention, no substitute.
4. Upload a scanned PDF → FR-2 rejection with its reason.
5. Summary returns nine fields with absences explicit; draft marks missing facts; both export.
6. Delete a document → every derived artifact gone, external targets reconciled, provable by test.
7. Every generation has an audit row and a cost row.
8. Pointing the frontend at it requires the §14 diff and nothing else.
9. `ruff` clean, tests green.

---

## 14. Frontend Integration — The Whole Diff

Not this phase's work; recorded so it is known to be small.

1. **`vite.config.ts`** — proxy `/api` to `http://127.0.0.1:8000` (no CORS in dev, no base-URL change).
2. **Don't register the MSW worker** when the real API is on (env flag).
3. **`lib/api.ts` upload** — send `FormData` instead of JSON:
   ```ts
   upload: (file: File, matterId: string) => {
     const fd = new FormData(); fd.append("file", file); fd.append("matterId", matterId);
     return request<LegalDocument>("/documents", { method: "POST", body: fd, headers: {} });
   }
   ```
   (`headers: {}` so the browser sets the multipart boundary.)

Everything else — conversations, chat, SSE, citations, status polling, matters — is wire-identical
to the mocks. The PDF panel, when Phase 5 of the frontend to-do is built, consumes
`/documents/{id}/file`.

---

## 15. Open Questions

1. **Does the Anthropic engine belong here or with the orchestrator?** My read of "only build the
   backend" is that it belongs with the orchestrator, so §8.3 is a seam. If you'd rather this
   phase ended with a real Claude call behind `llm_provider="anthropic"` — single call, citations
   enabled, no router and no workflows — say so and I'll add it as Phase 5b.
2. **The `citations` SSE event (§6).** Specified and emitted now, ignored by the frontend. Confirm
   that's the right order of operations, versus changing `sse.ts` in the same breath so the
   protocol never has two readers.
3. **Postgres or SQLite?** The BRD diagram says Postgres, the architecture diagram says SQLite for
   Phase 1. I've taken SQLite, with all SQL in repositories. Overrule if the course expects
   Postgres on the demo.
4. **Stopped messages.** A stopped turn persists its partial text with no marker, because
   `Message` has no `stopped` field and adding one is a frontend type change. Leave it, or add the
   field now while it's cheap?
5. **Seeded content.** Should the backend seed the three demo documents (MSA, NDA, complaint) on an
   empty database so the app looks alive on first boot, or start genuinely empty and make the
   empty states earn their keep?
