# Legal Lens — Frontend Implementation To-Do

> **Progress:** Phases 0–7 are built and running against mocks — Phase 5's document panel included, rendering the **text layer** rather than a PDF raster (see §3.5 and the amended Phase 5 items). Outstanding: the `/dev/styleguide` route, the matter switcher's move into the top bar, and the Phase 8 audits (keyboard, contrast, responsive, Lighthouse). Run with `cd frontend && npm install && npm run dev`.

> **Scope of this phase: UI only.** No backend, no orchestration, no AI layer. Every network call is mocked. The goal is a frontend that looks and feels finished, and that can be pointed at the real FastAPI backend later by changing one config flag.
>
> v0.1 · 2026-10-05 · approve or amend before implementation starts

---

## 1. Ground Rules

1. **Light theme only.** No dark mode in this phase. Tokens are structured so dark can be added later without touching components.
2. **Mock at the network boundary, not in components.** Components call the same API surface the real backend will expose (see §6). No component ever imports a fixture directly.
3. **Nothing fake-looking.** Realistic legal fixtures — a real-shaped MSA, an NDA, a complaint. No "Lorem ipsum", no "User 1 / Conversation 2".
4. **Every state gets designed**, not just the happy path: empty, loading, streaming, stopped, error, offline, rejected upload, and the abstention case ("the documents don't address this") which BRD NFR-1 makes a first-class outcome, not an error.
5. **Keyboard-first.** Everything reachable and operable without a mouse. WCAG 2.1 AA is a BRD requirement (NFR-12), not a nice-to-have.

---

## 2. Decisions Made — Overrule Any of These

You gave me a free hand, so these are decided rather than asked. Each is cheap to reverse now and expensive later.

| Area | Choice | Why |
|---|---|---|
| Framework | React 18 + TypeScript + Vite | Already committed in BRD D5 |
| Styling | Tailwind CSS v4 `@theme`, **no** `theme.ts` | One source of truth, consumed two ways. See §3.5 for the rule and why a TS theme object was rejected |
| Components | shadcn/ui (Radix primitives, copied into repo) | Accessible by default — focus traps, ARIA, keyboard nav come free. Not a dependency we're locked into |
| Client state | Zustand | Small, no boilerplate, no provider pyramid |
| Server state | TanStack Query | Caching, loading/error states, and it survives the switch to the real API untouched |
| API mocking | **MSW (Mock Service Worker)** | Intercepts at the network layer, so swapping to real FastAPI is a base-URL change. Fixtures never leak into components |
| Routing | React Router | Conversation URLs must be shareable/bookmarkable |
| Markdown | react-markdown + remark-gfm | Assistant messages are markdown |
| PDF | react-pdf (pdf.js) with a custom highlight overlay | Citation highlighting is the core trust feature — it has to be real, not simulated |
| Motion | CSS transitions, no animation library | Keeps the bundle lean; this UI needs restraint, not flourish |
| Tests | Vitest + React Testing Library on logic-bearing components | Full E2E deferred until there's a backend |

---

## 3. Design System

**Direction:** calm, dense, authoritative. Legal software earns trust by getting out of the way. Closer to Linear's restraint than to a consumer chat app. Generous whitespace, 1px borders instead of drop shadows, one accent colour used sparingly.

### 3.1 Colour tokens

```css
/* neutrals */
--canvas:         #F7F8FA;   --surface:        #FFFFFF;
--surface-sunken: #F1F3F7;   --border:         #E3E7ED;
--border-strong:  #CBD2DC;
--text:           #121926;   --text-muted:     #4B5565;   --text-subtle: #8B95A5;

/* brand — deep navy: authority without coldness */
--brand-900: #14233F;  --brand-700: #1D3A6B;  --brand-600: #26497F;
--brand-500: #3465A8;  --brand-100: #DCE6F5;  --brand-50:  #EEF3FB;

/* semantic */
--grounded-fg:  #166534;  --grounded-bg:  #F0FDF4;  --grounded-br:  #BBF7D0;
--attention-fg: #92400E;  --attention-bg: #FFFBEB;  --attention-br: #FDE68A;
--risk-fg:      #991B1B;  --risk-bg:      #FEF2F2;  --risk-br:      #FECACA;
--processing-fg:#3730A3;  --processing-bg:#EEF2FF;

/* citation highlight — one accent at two intensities */
--cite-bg: #E4ECF8;  --cite-bg-active: #C6D9F1;  --cite-line: #3465A8;
```

Amber is reserved for *needs attention*, so the active citation span does not borrow it: a
page that marked the span the reader jumped to in the same colour as an absent clause would
be saying two different things with one colour.

Semantic colours carry meaning consistently: **grounded** = cited and verified, **attention** = `[ATTORNEY INPUT REQUIRED]` and absent clauses, **risk** = abstention and rejected scans.

### 3.2 Type

| Role | Family | Notes |
|---|---|---|
| UI | Inter (fallback `system-ui`) | All chrome, labels, controls |
| Legal content | Source Serif 4 (fallback Georgia) | Draft output, summary field values, document text |
| Mono | JetBrains Mono (fallback `ui-monospace`) | IDs, page refs, metadata |

Serif for legal content is deliberate — drafts and summaries are documents, and should read like documents rather than like chat.

Scale: `11 · 12 · 13 · 14 · 16 · 18 · 22 · 28`. Weights **400 / 500 / 600 only**. Chat body 16px at line-height 1.6, measure capped at 72ch.

### 3.3 Space, shape, motion

- Spacing scale: `2 4 6 8 12 16 20 24 32 40 56 72`
- Radius: `xs 4 · sm 6 · md 8 · lg 12 · xl 16 · pill 999`
- Elevation: borders first. `shadow-1 0 1px 2px rgb(18 25 38 / .05)` for cards; `shadow-2 0 4px 12px rgb(18 25 38 / .08)` for popovers and menus only
- Motion: 120ms ease-out (hover/press), 180ms `cubic-bezier(.2,.8,.2,1)` (panels), 240ms (drawer). **All motion disabled under `prefers-reduced-motion`**
- Focus: 2px `--brand-500` ring at 2px offset, never removed

### 3.4 Layout

```
┌────────────┬──────────────────────────────────────┬──────────────┐
│  SIDEBAR   │  TOP BAR (56px)                      │              │
│  272px     ├──────────────────────────────────────┤  DOCUMENT    │
│            │                                      │  PANEL       │
│  threads   │  CHAT COLUMN  (max 760px, centred)   │  400–760px   │
│  grouped   │                                      │  resizable   │
│  by date   │                                      │  default 560 │
│            ├──────────────────────────────────────┤              │
│            │  COMPOSER (sticky bottom)            │              │
└────────────┴──────────────────────────────────────┴──────────────┘
```

Breakpoints: `<768px` sidebar + document panel both become full-screen overlays · `768–1024px` sidebar is an overlay drawer, document panel overlays · `1024–1440px` sidebar docked, document panel overlays · `>1440px` all three columns docked.

---

### 3.5 Styling architecture — Tailwind `@theme`, not a `theme.ts`

The choice was between Tailwind utilities over CSS custom properties and a TypeScript theme
object consumed through inline styles or CSS-in-JS. **Tailwind v4's `@theme` block wins, and
there is no theme object.** Every token is declared once in `styles/globals.css`, and Tailwind
emits each one as *both* a real CSS custom property on `:root` and a utility class. One
declaration, two consumption paths:

| Need | Use | Example |
|---|---|---|
| Styling markup | the utility class | `bg-cite-bg`, `text-ink-muted`, `w-sidebar` |
| A value JS must compute with | `cssVar()` in `lib/tokens.ts` | canvas fills, a future PDF highlight overlay |
| A dimension produced by runtime arithmetic | a constant in `lib/tokens.ts` | the panel's drag bounds |

Why not a theme object:

- **A theme object is a second source of truth for colour, and colour drifts silently.**
  Nothing fails when a hex in TS stops matching the CSS — the UI just quietly stops matching
  itself. `lib/tokens.ts` was exactly that: a hand-copied table of hex literals mirroring the
  `@theme` block. It is now a reader of the live values, and the duplication is gone.
- **Dark mode is cheaper in CSS.** A theme is a selector that redefines the same custom
  property names. Nothing that consumes a token changes — which is what §1's "dark can be added
  later without touching components" actually requires. A TS object would need every consumer to
  re-render through a provider.
- **Production cost.** Utilities are compiled and purged at build time; a runtime theme object
  means style injection, a provider in the tree, and a worse SSR story if this ever needs one.
- **Modularity lands in the same place either way.** The seam that matters is "components never
  hard-code a value", and a class referencing a token enforces that as strictly as a TS lookup
  — with a build-time check instead of a runtime one.

The rule to hold, and the one worth reviewing in PRs: **no raw hex, no ad-hoc spacing, and no
new token in two places.** A dimension consumed as a class belongs in `@theme`; a dimension
computed at runtime belongs in `lib/tokens.ts`; nothing belongs in both.

---

## 4. Feature Inventory

What-all the UI needs, derived from the BRD and the architecture diagram.

**A · Shell** — collapsible sidebar, top bar, matter switcher, command palette (`⌘K`), toast host, error boundary
**B · Conversations** — list grouped by Today / Yesterday / Previous 7 days / Older, search, new chat, inline rename, delete with confirm, active highlighting, hover actions
**C · Chat thread** — user/assistant turns, markdown, streaming with cursor, stop, copy, regenerate, scroll-to-bottom on scroll-up, empty state with suggested prompts per intent, detected-intent badge, **abstention notice** styled distinctly from an error
**D · Composer** — auto-growing textarea, document scope chips, attach, send/stop, `Enter` send + `Shift+Enter` newline, character/token hint, disabled reasons surfaced
**E · Citations** — inline numbered chips, hover popover showing `cited_text`, click opens the document panel at that page with the span highlighted, per-message groundedness badge, citation list under long answers
**F · Document panel** — PDF render, page nav, zoom, highlight overlay, jump-to-citation, resize handle, close
**G · Document library** — upload dropzone, grid/list, per-document ingest status (queued / processing / ready / failed), **rejected-scan notice** explaining OCR is Phase 2 (FR-2), delete with confirm, filter by matter
**H · Summary** — the 9 BRD fields as a structured card (parties; term & renewal; obligations; payment; termination triggers; governing law & venue; liability & indemnification; unusual provisions; open items), each with its citation, **absent clauses rendered explicitly** rather than omitted
**I · Draft** — serif document rendering, "unreviewed draft" banner, `[ATTORNEY INPUT REQUIRED]` placeholders visually marked, export menu (.docx / Markdown), copy
**J · States** — skeletons (never spinners for content), empty, error with retry, offline banner
**K · A11y** — landmarks, live region for streaming, focus management on panel open/close, visible focus, AA contrast throughout

---

## 5. Routes

| Route | Screen |
|---|---|
| `/` | redirect → most recent conversation, or `/chat/new` |
| `/chat/new` | empty state + suggested prompts |
| `/chat/:conversationId` | chat thread |
| `/chat/:conversationId?doc=:docId&page=:n` | thread with document panel open at a cited page |
| `/documents` | document library |
| `/documents/:documentId` | full-width document viewer |
| `/settings` | minimal — appearance, shortcuts, about |

---

## 6. Mock API Contract

MSW handlers implement exactly the endpoints in the architecture diagram, so the swap to real FastAPI is a base-URL change.

```
POST   /conversations                 → create thread          (the "New Chat" endpoint)
GET    /conversations                 → sidebar list
GET    /conversations/:id/messages    → thread history
PATCH  /conversations/:id             → rename
DELETE /conversations/:id             → delete

POST   /chat/message                  → accept turn, return message id
GET    /chat/stream                   → SSE token stream
POST   /chat/stop                     → halt generation
GET    /chat/:msgId/citations         → citation payload

POST   /documents                     → upload
GET    /documents                     → library
GET    /documents/:id/status          → ingest state
GET    /documents/:id/page/:n         → page text + charCount  (what the viewer renders)
GET    /documents/:id                 → one document
DELETE /documents/:id                 → delete

GET    /matters                       → matter list
```

**Streaming:** MSW v2 returns a `ReadableStream` for `/chat/stream`, chunked on a timer with a realistic cadence and a variable first-token delay. If SSE through MSW proves awkward, fall back to a local async-generator transport behind the same `lib/sse.ts` interface — components must not notice.

**Page fixtures are derived, not written.** `fixtures/pages.ts` collects every `citedText`
from the message fixtures and inserts it verbatim into the page its citation names, then pads
the page with deterministic clause text. Writing page text by hand would eventually paraphrase
a cited span, producing a chip that opens the right page and highlights nothing — the exact
silent failure the product exists to prevent. A probe asserts all 21 fixture citations resolve.

**Fixtures to build** (`src/mocks/fixtures/`): 3 documents (an MSA ~40pp, an NDA ~8pp, a complaint ~15pp) with real-shaped clause text and page anchors; 6 conversations covering all three intents; one unanswerable question → abstention; one document stuck in `processing`; one scanned PDF → `failed` with the FR-2 rejection reason.

---

## 7. Folder Skeleton

```
frontend/
├── index.html · package.json · tsconfig.json · vite.config.ts
├── public/mockServiceWorker.js
└── src/
    ├── main.tsx · App.tsx
    ├── app/           router.tsx · providers.tsx · layouts/AppShell.tsx
    ├── components/
    │   ├── ui/            Button Input Textarea Dialog DropdownMenu Tooltip Popover
    │   │                  Tabs ScrollArea Skeleton Toast Badge Separator Resizable
    │   ├── shell/         Sidebar SidebarToggle TopBar MatterSwitcher CommandPalette
    │   ├── conversations/ ConversationList ConversationGroup ConversationItem
    │   │                  NewChatButton ConversationSearch RenameDialog DeleteDialog
    │   ├── chat/          ChatThread MessageList UserMessage AssistantMessage
    │   │                  MessageActions StreamingCursor ScrollToBottom
    │   │                  EmptyState SuggestedPrompts IntentBadge AbstentionNotice
    │   ├── composer/      Composer ScopePicker AttachmentChip SendButton StopButton
    │   ├── citations/     CitationChip CitationPopover CitationList GroundednessBadge
    │   ├── documents/     DocumentPanel PdfViewer PageToolbar HighlightLayer
    │   │                  DocumentLibrary DocumentCard UploadDropzone
    │   │                  IngestStatus RejectedScanNotice
    │   ├── summary/       SummaryCard SummaryField AbsentFieldRow
    │   ├── draft/         DraftDocument DraftBanner PlaceholderMark ExportMenu
    │   └── common/        ErrorBoundary LoadingSkeleton ConfirmDialog OfflineBanner
    ├── hooks/      useStreamingMessage useAutoScroll useResizablePanel
    │               useKeyboardShortcuts useMediaQuery
    ├── lib/        api.ts sse.ts cn.ts format.ts constants.ts
    ├── mocks/      browser.ts handlers/ fixtures/ stream.ts
    ├── stores/     useUIStore.ts useConversationStore.ts useDocPanelStore.ts
    ├── styles/     globals.css tokens.css
    └── types/      api.ts domain.ts
```

---

## 8. Build Phases

### Phase 0 — Scaffold
- [x] Vite + React + TS in `frontend/`, strict mode on
- [x] Tailwind, path aliases (`@/`), ESLint + Prettier
- [x] MSW installed, worker registered, dev-only
- [x] Router + providers skeleton, app boots to a blank shell

### Phase 1 — Design system
- [x] `tokens.css` — every token from §3 as CSS custom properties
- [x] Tailwind theme mapped onto the tokens (no raw hex in components, ever)
- [x] Fonts loaded with `font-display: swap`
- [x] UI primitives from shadcn, restyled to the token set
- [x] Focus-ring and reduced-motion globals
- [ ] A `/dev/styleguide` route rendering every primitive and every state — this is the reference for the rest of the build

### Phase 2 — App shell
- [x] `AppShell` three-column grid, sidebar docked
- [x] Sidebar collapse/expand, state persisted to `localStorage`
- [x] Top bar: editable conversation title (in place, not a dialog), document-panel toggle
- [ ] Top bar: matter switcher — still in the sidebar, where it works; moving it is cosmetic
- [x] Responsive: drawer behaviour at each breakpoint in §3.4
- [x] `⌘K` command palette: new chat, jump to conversation, open document, toggle panel, navigate

### Phase 3 — Conversations
- [x] MSW handlers + fixtures for the conversation endpoints
- [x] Date-grouped list with sticky group headers
- [x] Search filter, active-state highlight
- [x] New chat, rename dialog, delete with confirm
- [x] Loading skeleton + empty state
- [x] Optimistic rename and delete, with rollback on failure (sidebar and top-bar rename)

### Phase 4 — Chat + streaming  ← *the core*
- [x] Message list  *(virtualisation deferred — fixture threads are short)*
- [x] User and assistant bubbles; markdown with GFM tables and lists
- [x] Streaming via `useStreamingMessage`: token cadence, blinking cursor, stop button
- [x] **No layout shift while streaming** — reserve height, don't reflow the thread
- [x] Auto-scroll that yields the moment the user scrolls up, plus a scroll-to-bottom pill
- [x] Composer: auto-grow to 8 rows then scroll, `Enter`/`Shift+Enter`, scope chips
- [x] Intent badge (ask / summary / draft) surfaced on the turn
- [x] Empty state with per-intent suggested prompts
- [x] Abstention rendering — calm and informative, *not* an error style
- [x] Message actions: copy, regenerate, good/bad feedback
- [x] `aria-live="polite"` region so streamed text reaches screen readers

### Phase 5 — Citations + document panel
- [x] Inline citation chips, numbered per message
- [x] Hover popover with `cited_text` and page reference
- [x] Document panel: page nav, zoom, drag **and keyboard** resize, docked ⟷ overlay at 1440px
- [x] **Text-layer rendering, not react-pdf** — amended decision, see the note below
- [x] Highlight resolved by locating the cited span in the page text, whitespace-tolerant
- [x] Says so explicitly when the span cannot be found, rather than marking approximately
- [x] Click a chip → panel opens at that page, span highlighted, URL updated (`?doc=&page=`)
- [x] Hover/focus previews the passage, click opens the source — preview and verification split
- [x] Focus moves to the panel on open, returns to the chip on close; Escape closes; Tab is
      trapped while the panel is modal
- [ ] react-pdf raster view, once the backend serves `GET /documents/:id/file`
- [x] Per-message groundedness badge

**Why the panel renders the text layer instead of a PDF raster.** react-pdf was the committed
choice on the assumption that the panel shows the file. It cannot yet: the fixtures have no
real PDFs, and the backend contract (`docs/backend-todo.md` §5.3) serves page **text** plus the
original file, not server-rendered page images. Rendering the text layer turned out to be the
better default rather than a stopgap:

- The text layer is *what the model was shown and what a citation resolves against*. The reader
  verifies the same artefact the answer was grounded in, not a parallel rendering of it.
- Highlighting is a string match in that text, not a guess at coordinates — so it is exact, or
  it is honestly absent. A coordinate overlay can land in the wrong place and still look right.
- It works for DOCX and TXT, which have no pages to raster at all.

The raster view stays on the list as an addition for PDFs, behind the same `PageSurface` props,
for the reader who needs to see the signature block and the letterhead.

### Phase 6 — Summary + Draft
- [x] `SummaryCard` — all 9 fields, each with its citation chip
- [x] Absent clauses rendered in `attention` style with an explicit "not found in this document"
- [x] `DraftDocument` in serif, with document structure
- [x] "Unreviewed draft — requires attorney review" banner, persistent
- [x] `[ATTORNEY INPUT REQUIRED]` rendered as a visually distinct inline mark
- [x] Export menu (.docx / Markdown) — UI + mocked download

### Phase 7 — Document library
- [x] Upload dropzone: drag-over state, type/size validation, per-file rejection reasons
- [x] Indeterminate upload progress (honest: the mock transport reports no percentage)
- [x] Library grid with per-document ingest status and polling simulation
- [x] Rejected-scan notice explaining OCR is Phase 2, with a clear next step
- [x] Document detail route (`/documents/:id`), full-width viewer sharing the panel's renderer
- [x] Delete with confirm, naming what the purge covers; optimistic with rollback
- [x] Matter filter

### Phase 8 — Polish
- [ ] Keyboard-only pass over every flow
- [ ] Contrast audit — all text ≥ AA
- [ ] Every empty / error / offline state present and designed
- [x] `prefers-reduced-motion` honoured everywhere
- [ ] Responsive QA at 768 / 1024 / 1440 / 1920
- [x] Skeletons replace spinners for content loading
- [ ] Lighthouse a11y ≥ 95

---

## 9. Definition of Done

1. Every flow in §4 is operable end-to-end against mocks.
2. Keyboard-only navigation works for chat, citations, and upload.
3. No raw hex or ad-hoc spacing in any component — tokens only.
4. No component imports a fixture; everything goes through `lib/api.ts`.
5. Streaming causes no layout shift and no scroll jank.
6. The abstention case and the rejected-scan case are both visible in the running app.
7. Pointing at a real backend requires changing only the API base URL and disabling MSW — plus
   the multipart upload change recorded in `docs/backend-todo.md` §14.

**Verified mechanically:** `npm run typecheck` and `npm run build` clean; all 21 fixture
citations resolve on the page they name. **Not yet verified:** the interactive flows — no E2E
runner is installed, so Phase 8's keyboard, contrast and responsive passes remain manual.

---

## 10. Open Questions

1. **Matters in the MVP UI** — the BRD keeps `matter_id` in the data model but says Phase 1 is single-user. Should the matter switcher be visible now, or hidden behind a flag until Phase 2?
2. **Draft editing** — is the draft read-only with export, or inline-editable in the browser? Editable is substantially more work (rich-text editor) and may belong in Phase 2.
3. **Logo / wordmark** — do you have one, or should I set type-only branding for now?
4. ~~**Command palette** — worth the build in this phase, or defer?~~ **Built.** Forty lines on
   Radix Dialog rather than a combobox dependency, and it turned out to be where "open document
   X" naturally lives, which the sidebar had nowhere to put.
5. **Draft export** still downloads nothing — the backend's `POST /messages/:id/export` exists in
   the contract but is not wired, since it needs real bytes to hand the browser.
