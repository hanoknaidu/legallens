# Frontend ⇄ Backend Integration — Plan

Status: **Phases 0–6 implemented.** Written 2026-10-06, updated 2026-10-19.

Progress: **Phase 0 ✅ · 1 ✅ · 2 ✅ · 3 ✅ · 4 ◐ · 5 ✅ · 6 ✅**

Phase 4 is complete except one item that needs a human at a browser: clicking a
citation chip through to a highlighted span. Everything beneath it is verified.

**The orchestration layer landed behind this on 2026-10-19** —
`LEGAL_LENS_LLM_PROVIDER=orchestrator`, documented in
[workflows-plan.md §9.3](workflows-plan.md). It changed no route, no schema and
nothing in the frontend, which is what §1's agreed contract was for. It did change the
generation seam: a provider can now own its retrieval, because the pages sent and the
conclusions permitted are one decision.

---

## 0. Read this first: the starting position is not what it looks like

The integration was framed as "the frontend has mocks, the backend has the APIs, join them."
The second half isn't true, and it changes the shape of the work.

**The backend exposes two routes.** Verified against the running app's own OpenAPI document:

```
GET /api/health
GET /api/metrics
```

That is the whole surface. `src/legal_lens/` contains `main.py`, `config.py`, `schemas.py`,
`observability.py` and `db/session.py` — and `api/`, `ingestion/` and `ai/` exist as **empty
directories**. There are no routers, no repositories, no ingestion, no retrieval, no model
calls. `main.py` itself was written on 2026-10-06 to make the service boot at all; before that
`uvicorn legal_lens.main:app` failed with `ModuleNotFoundError`.

So this is not a wiring job with a day of work in it. **It is building the backend API, which
is the bulk of [backend-todo.md](backend-todo.md), and then a genuinely small frontend diff.**
The honest split:

| Side | Work |
|---|---|
| Backend | ~95% — every route in §5 of backend-todo.md, plus repositories, ingestion, retrieval, generation |
| Frontend | ~5% — a Vite proxy, a mock toggle, one `FormData` change, and better error surfacing |

The good news is real: **the two sides already agree on the wire format**, so almost none of
this is contract negotiation. See §1.

### What this plan is not

It does not re-plan the backend. [backend-todo.md](backend-todo.md) already specifies every
route, status code and invariant, and it is good. This document is the **integration-ordered**
view of that work: which pieces to build in what order so the frontend comes alive incrementally
and each step is verifiable in the browser, plus the contract gaps that only show up when you
hold the two sides side by side.

---

## 1. The contract is already agreed — and that is the asset here

[`schemas.py`](../backend/src/legal_lens/schemas.py) and
[`domain.ts`](../frontend/src/types/domain.ts) were written against each other. `Wire` sets
`alias_generator=to_camel`, FastAPI serialises responses by alias by default, and
`populate_by_name=True` means requests accept either casing. Field-by-field, these match:

`Matter` · `LegalDocument` · `DocumentStatus` · `Citation` · `SummaryField` · `DraftPayload` ·
`Message` · `Conversation`

`API_BASE` is the relative string `"/api"`
([constants.ts:3](../frontend/src/lib/constants.ts#L3)), so a dev proxy needs **no frontend code
change at all** — §14 of backend-todo.md is right about that.

The mock handlers are the de-facto contract and they already sit at the real paths
(`/api/matters`, `/api/conversations`, `/api/chat/message`, …). Treat
[`src/mocks/handlers/`](../frontend/src/mocks/handlers/) as the acceptance spec: **a route is
done when the frontend behaves identically with the mock off.**

---

## 2. Contract gaps found by comparing the two sides

These are the things that will break on the day of wiring. Each is small; none is discoverable
from either side alone. **Fix these as you build the route, not afterwards.**

| # | Gap | Where | Fix |
|---|---|---|---|
| G1 | **`DocumentPage` schema does not exist in the backend.** The frontend's document viewer fetches `{documentId, page, text, charCount}`; `schemas.py` has no such model. | [api.ts:74](../frontend/src/lib/api.ts#L74) wants it; `schemas.py` lacks it | ✅ **Done** — `DocumentPage` added to `schemas.py` |
| G2 | **Upload sends metadata, not bytes.** The mock never needed the file, so `api.documents.upload` posts JSON `{filename, sizeBytes, matterId}`. The real route needs `multipart`. | [api.ts:76](../frontend/src/lib/api.ts#L76) | ✅ **Done** — `FormData` with `headers: {}`; the MSW handler reads multipart too |
| G3 | **`POST /chat/stop` will 422.** The frontend sends no body but *does* send `Content-Type: application/json`. A FastAPI handler typed `body: StopRequest` rejects an empty body. | [api.ts:60](../frontend/src/lib/api.ts#L60) | ✅ **Done** — `StopRequest | None = None`; pinned by a test |
| G4 | **`GET /documents/{id}` is missing from the backend spec.** §5.3 lists create/list/status/file/page/delete but not fetch-one — and the frontend calls it in two places. | [DocumentPage.tsx:45](../frontend/src/pages/DocumentPage.tsx#L45), [DocumentPanel.tsx:45](../frontend/src/components/documents/DocumentPanel.tsx#L45) | Add the route |
| G5 | **Naive datetimes will render in the wrong timezone.** The frontend does `new Date(iso)`; JS reads a stamp with no offset as *local*. Mocks emit `.toISOString()` (with `Z`). | [format.ts](../frontend/src/lib/format.ts) | ✅ **Done** — `clock.py` emits `Z`-suffixed aware UTC; pinned by a regex test |
| G6 | **Error bodies are discarded.** `ApiError` keeps only the status and a synthetic message, so the problem+json `detail` is thrown away — including the FR-2 scanned-PDF reason and the 409 that *names* the not-ready document. | [api.ts:23](../frontend/src/lib/api.ts#L23) | ✅ **Done** — `ApiError.fromResponse` parses `code`/`detail`/`requestId` |
| G7 | **MSW registration is gated on `DEV`, not on a mock flag.** Pointing at the real API in dev is currently impossible without editing code. | [main.tsx:9](../frontend/src/main.tsx#L9) | ✅ **Done** — `USE_MOCKS` reads `VITE_USE_MOCKS` |
| G8 | **Matter id must be literally `m-001`.** The frontend hard-falls-back to it. A different seed id silently breaks new conversations. | [AppShell.tsx:23](../frontend/src/app/layouts/AppShell.tsx#L23) | ✅ **Done** — seeded as `m-001`, pinned by a test |
| G9 | **Export has no frontend caller.** The backend spec has `POST /messages/{id}/export`; `api.ts` has no export method and no UI is wired to one. | — | ✅ **Done** — `exportMessage`/`saveBlob` in `api.ts`, wired to the Export menu that already existed in `DraftDocument.tsx` |
| G10 | **The `citations` SSE event has no reader.** backend-todo §15.2 plans to emit it; `sse.ts` handles only `meta`, `token`, `done` and silently drops anything else. | [sse.ts:53-55](../frontend/src/lib/sse.ts#L53-L55) | ✅ **Decided (D2)** — not emitted; citations arrive complete on `meta`, re-sent as each one is found |

---

## 3. The streaming contract, exactly as the frontend expects it

This is the highest-risk route, so it is written out rather than referenced. Getting it subtly
wrong produces a chat that hangs with no error.

**Two requests, with server-held state between them:**

1. `POST /api/chat/message` → `{userMessage, assistantMessageId, intent}`. Persists the user
   turn, classifies intent, registers a **pending** assistant turn. Nothing of the assistant
   turn is persisted yet.
2. `GET /api/chat/stream?messageId=<assistantMessageId>` → `text/event-stream`. Generation
   happens here.

**Frame format** — `event:` and `data:` lines, blank line between frames, `data` is one line of
JSON. The parser splits on `\n\n` and ignores any frame missing either line
([sse.ts:44-56](../frontend/src/lib/sse.ts#L44-L56)).

**Event order is load-bearing:**

- `meta` — **first, exactly once, before any token.** Carries
  `{id, intent, grounded, abstained, citations, summary?, draft?}`. Citations arrive *here*,
  complete, not at the end: the renderer resolves `{{cite:n}}` markers against them as tokens
  arrive, and a marker with no citation renders as a dead chip.
- `token` — `{text}`, appended verbatim. **For `summary` and `draft` intents the mock sends no
  tokens at all** — the structured payload rides on `meta` and resolves as a unit, because a
  half-rendered summary table reads as broken. Match that.
- `done` — `{id}`. The frontend stops its spinner here. **If this never arrives the UI hangs**;
  send it in a `finally`.

**Other requirements:** `Content-Type: text/event-stream`, `Cache-Control: no-cache`. Pending
turns expire after 120 s; an unknown or expired id is `404`. Persist the assistant turn and its
citations **in one transaction** when the stream completes. `POST /chat/stop` aborts via the
client's `AbortSignal` — the partial text persists with audit `outcome: "stopped"`.

> **Single worker only.** The pending-turn registry is in-process and SQLite takes one writer.
> Never add `--workers` ([backend-todo.md:65](backend-todo.md#L65)).

---

## 4. Phases

Ordered so the frontend gets visibly more real at each step, and so each phase is verifiable in
the browser with the mock off. Backend cross-cutting pieces (`errors.py`, `deps.py`,
`services.py`, repositories) land in Phase 1 because everything after depends on them.

### Phase 0 — Make the seam real (frontend only, ~1 hour)

The only phase that is pure integration. Do it first: it proves the proxy path end to end
against `/api/health`, which already works, so nothing here is blocked on backend routes.

- [x] `vite.config.ts`: proxy `/api` → `http://127.0.0.1:8000` (`changeOrigin: true`). Kills
      CORS in dev entirely and keeps `API_BASE` as `"/api"`
- [x] `main.tsx`: register MSW only when `import.meta.env.VITE_USE_MOCKS !== "false"` (G7)
- [x] `.env.development` with `VITE_USE_MOCKS=true`; `.env.development.local` documented as the
      way to flip to the real API
- [x] Surface backend health in the UI — a small degraded banner reading `/api/health`. Cheap,
      and it makes "is the backend even up?" answerable without a terminal
- [x] **Verify:** with mocks off, the app loads, the health banner reflects real backend state,
      and every data route 404s cleanly rather than hanging

### Phase 1 — The read-only spine: matters + conversations

First real data in the UI. The sidebar stops being fiction.

- [x] `errors.py` — `ApiProblem`, handlers emitting problem+json with `code` and `requestId`
      (§5.8)
- [x] `deps.py` — `get_db`, `get_services`, `current_owner_id` (the Phase 2 auth seam; returns
      `PHASE1_OWNER_ID`)
- [x] `services.py` — container built at lifespan
- [x] `db/repositories/` — `matters`, `conversations`, `messages`. **Every query takes
      `owner_id`**; write the owner-scoping regression test now, while it is cheap
- [x] Seed one matter on first boot, id **`m-001`** (G8)
- [x] `GET /matters`
- [x] `GET /conversations` (updatedAt desc) · `POST` · `GET /{id}/messages` (sequence asc) ·
      `PATCH /{id}` · `DELETE /{id}` (cascades)
- [x] Title behaviour must mirror the mock: omitted → `"New conversation"`, replaced by the
      first user turn truncated to 52 chars + `…`
      *(`derive_title` and `ConversationRepository.touch` are written and tested at the
      unit level, but nothing calls them until `POST /chat/message` lands in Phase 3.)*
- [x] Frontend: parse problem+json into `ApiError` (G6)
- [x] **Verify:** mocks off — sidebar lists real conversations, create/rename/delete persist
      across reload. Conversations will be empty and messages blank; that is correct here

### Phase 2 — Documents + ingestion

The first phase with real user content. Largest backend lift.

- [x] `ingestion/` — parse (pypdf, python-docx, txt), page extraction, chunking
      (`chunk_target_chars`, `chunk_overlap_chars`)
- [x] FR-2 scanned-PDF rejection: `status: "failed"` + verbatim reason, **in one constant shared
      with the mock's wording**. The row persists; nothing is indexed
- [x] `db/repositories/documents`
- [x] Add `DocumentPage` to `schemas.py` (G1)
- [x] `POST /documents` (multipart, `201`, status `queued`) · `GET /documents?matterId=` ·
      `GET /documents/{id}` (G4) · `GET /{id}/status` · `GET /{id}/page/{n}` ·
      `GET /{id}/file` · `DELETE /{id}`
- [x] Enforce `413` / `415` / `422` to match the client-side gate the frontend already applies
- [x] Frontend: `api.ts` upload → `FormData` with `headers: {}` (G2)
- [x] **Verify:** mocks off — drag a real PDF in, watch `queued → processing → ready` via the
      existing 2 s poll ([DocumentsPage.tsx:102](../frontend/src/pages/DocumentsPage.tsx#L102)),
      open the viewer and read real page text. Upload a scanned PDF and see the rejection notice
      with its reason. Delete and confirm it stays deleted

### Phase 3 — Chat + SSE

Where the app becomes itself. Build strictly to §3 above.

- [x] Retrieval: `whole_document_token_budget` decides whole-corpus vs RAG per request; hashing
      embeddings and the in-memory vector store are fine here — both are already the config
      defaults, so this runs offline
- [x] Intent router (`ask` / `summary` / `draft`)
- [x] Generation behind `llm_provider`. **Build `echo` first** — a deterministic stub that
      exercises the whole SSE path, citation markers and persistence with no API key and no cost.
      Do not debug streaming and model calls at the same time
- [x] In-process pending-turn registry, 120 s expiry
- [x] `POST /chat/message` · `GET /chat/stream` · `POST /chat/stop` (optional body, G3) ·
      `GET /chat/{msgId}/citations`
- [x] Persist assistant turn + citations in one transaction on stream completion
- [x] Abstention (FR-9) and `grounded` as first-class outcomes, not errors
- [x] **Verify:** mocks off, `llm_provider=echo` — ask a question, watch tokens stream, press
      stop mid-stream and confirm the partial persists. Then `llm_provider=anthropic` with a key:
      a real cited answer whose quoted text is verifiably on the cited page

      *(The `echo` half is verified end to end. The Anthropic provider is written —
      native citations via custom-content documents, so page numbers come from the API
      rather than from model prose — but has **not been run against the live API**: it
      needs `LEGAL_LENS_ANTHROPIC_API_KEY` and costs money per call.)*
### Phase 4 — Citations, summary, draft end to end

> **Partly landed with Phase 3.** `{{cite:n}}` markers, the nine summary fields with
> explicit absences, and the `[ATTORNEY INPUT REQUIRED]` round trip are implemented
> and tested under `llm_provider=echo`, and a test asserts every citation's
> `citedText` appears verbatim on the page it names. What is left here is the
> **real-provider** half: running the same assertions against `llm_provider=anthropic`
> with a key, and clicking a chip through to a highlighted span in the browser.



- [x] `{{cite:n}}` markers emitted in content, numbering consistent with the `meta` citations
- [x] Nine summary fields in the fixed order from
      [constants.ts:12](../frontend/src/lib/constants.ts#L12); **absent clauses are `value: null`,
      never omitted** (FR-13)
- [x] Draft payloads preserve `[ATTORNEY INPUT REQUIRED]` verbatim through the round trip
- [ ] **Verify:** click a citation chip → viewer opens the right document at the right page with
      the span highlighted. This is the product's core claim; test it by hand
      *(**still outstanding, and only a human can do it.** What is verified is the
      layer beneath: a live Opus call returned 2/2 citations whose text appears
      verbatim on the page each one names, asserted programmatically. Nobody has
      clicked a chip in a browser — I have no browser tooling — so the last link,
      chip → viewer → highlighted span, is unconfirmed.)*

### Phase 5 — Export + audit

- [x] `POST /messages/{id}/export` — docx + markdown, FR-17 notice **inside the file**
- [x] `audit` + `cost` rows for every generation (FR-20)
- [x] `GET /audit` · `GET /audit/export`
- [x] Frontend: add `api.exports.*` and wire the message action (G9)

### Phase 6 — Decommission the mocks

- [x] Keep `src/mocks/` — it is the contract fixture and the offline demo path. Do **not** delete
- [x] Flip the `VITE_USE_MOCKS` default to `false`
- [x] README: both run modes, the single-worker constraint, and `.env` setup
- [x] Pending-deletion count in `/health` checks (NFR-8), per the §5.7 spec *(landed with Phase 2)*

---

## 4a. Deviation from backend-todo.md §5.1 — `title` is required on create

**Decided 2026-10-10, at the user's request.** `POST /conversations` now requires a
`title` and returns `422` without one. §5.1 of [backend-todo.md](backend-todo.md)
says "Title omitted → `New conversation`"; that is no longer true, and this note is
here so the spec is not left quietly contradicted.

What changed and why it is safe:

- The **client** now decides the initial name, including the placeholder for an
  unnamed conversation, so there is exactly one place that decision lives.
- Auto-naming is unchanged: a conversation is renamed by its first user turn while
  its stored title still equals the placeholder, and a title the user chose is still
  never overwritten. Both are covered by tests.
- Blank and whitespace titles are refused on **both** create and rename, and both
  share one `TITLE_MAX_LENGTH = 200`. Before this they disagreed — `POST` accepted a
  300-character title that `PATCH` rejected with a 422, so a conversation could be
  created in a state it could not be renamed back into.

**The one fragility this introduces:** the placeholder string now exists in both
codebases — `DEFAULT_TITLE` in `db/repositories/conversations.py` and
`DEFAULT_CONVERSATION_TITLE` in `frontend/src/lib/constants.ts` — and they must stay
identical or auto-naming silently stops, with nothing raised. `tests/
test_conversation_titles.py::test_the_placeholder_title_matches_the_frontend_constant`
reads the frontend file and asserts they agree; it was verified to fail on a
deliberate drift.

The MSW handler mirrors the 422 so the mock cannot accept a request the real backend
would reject.

---

## 5. Cross-cutting, easy to forget

- [ ] **Owner scoping from the first query, not retrofitted.** Every repository method takes
      `owner_id`. A query with a different owner returns nothing — the Phase 2 auth test, written
      now
- [ ] **One rejection-reason constant** shared between backend and mock, or the two drift
- [x] **`rate_limit_per_minute` is configured but unenforced** — add to `middleware.py`, and make *(done: middleware.py, sliding window, health/metrics/stream exempt)*
      sure 429 doesn't look like a hang to the frontend
- [ ] **Request id already flows** (`X-Request-Id`, bound to the log context var). Carry it into
      `ApiError` so a user-visible failure maps to a log line
- [ ] `.env.example` for the backend — `LEGAL_LENS_ANTHROPIC_API_KEY` is the one that matters
- [ ] Keep `ruff` clean and tests green per DoD §13.9

---

## 6. Decisions I need from you

**D1 and D2 block Phase 3.** The rest can be answered when their phase arrives.

- ~~**D1~~ **DECIDED** — Does the real Claude call land in this phase?** backend-todo §15.1 leaves the Anthropic
  engine to an orchestrator and treats §8.3 as a seam. But a frontend wired to `echo` is wired to
  a stub: it proves the plumbing, not the product. *My recommendation:* build `echo` in Phase 3,
  then add a single real Anthropic call with citations enabled behind
  `llm_provider="anthropic"` — no router, no workflows. That makes Phase 3's verification a real
  cited answer.
- ~~**D2~~ **DECIDED** — The `citations` SSE event** (§15.2, G10). The frontend ignores unknown events, so
  emitting it is harmless but pointless. *My recommendation:* don't emit it. Citations already
  arrive complete on `meta`, which is what the renderer actually uses. One reader, one protocol.
- **D3 — Seed the three demo documents on an empty database?** (§15.5) *My recommendation:* no.
  Ingest them through the real upload path instead — it exercises the pipeline and the empty
  states stay honest. A seed script that bypasses ingestion hides exactly the bugs this phase
  exists to find.
- **D4 — `stopped` on `Message`?** (§15.4) A stopped turn currently persists partial text with no
  marker. Adding the field is a one-line change in both `schemas.py` and `domain.ts` *now*, and a
  migration later. *My recommendation:* add it in Phase 3.
- **D5 — SQLite or Postgres?** (§15.3) SQLite is chosen and all SQL is in repositories. Only
  overrule if the course expects Postgres on the demo.

---

## 7. Definition of done

1. `VITE_USE_MOCKS=false` and the app is fully functional — no MSW, no fixtures.
2. Upload a real 40-page MSA → `ready` → ask a question → a streamed cited answer whose quoted
   text is verifiably on the cited page.
3. Ask something the documents don't answer → abstention, no substitute answer.
4. Scanned PDF → FR-2 rejection with its reason shown in the UI.
5. Summary returns nine fields with absences explicit; draft marks missing facts; both export.
6. Delete a document → every derived artifact gone, provable by test.
7. Every generation has an audit row and a cost row.
8. Stop mid-stream → partial persists, audit says `stopped`.
9. Reload at any point → UI state matches the database.
10. `ruff` clean, tests green, `npm run typecheck` clean.
