# End-to-end tests

Playwright tests that drive the real UI in a real browser against the real backend.
No mocks: `global-setup.ts` refuses to run if `VITE_USE_MOCKS=true`, if either server
is down, or if the model provider is not usable — each of those turns the whole suite
into a wall of unrelated failures, so each is named instead.

## Running them

Three terminals.

```bash
# 1 — backend. Any provider works; this is the full pipeline.
cd backend
LEGAL_LENS_LLM_PROVIDER=orchestrator \
LEGAL_LENS_INTENT_ROUTER=model \
LEGAL_LENS_RATE_LIMIT_PER_MINUTE=100000 \
  uv run uvicorn legal_lens.main:app --port 8000

# 2 — frontend
cd frontend && npm run dev

# 3 — the tests
cd e2e && npm install && npx playwright install chromium && npm test
```

`LEGAL_LENS_RATE_LIMIT_PER_MINUTE` is raised for the run. The suite makes direct API
reads alongside the browser's own traffic and navigates faster than a person, so it
exceeds the 60/minute default and gets 429s that look like application failures. The
app by itself does not come close — measured at **14 requests a minute** for ordinary
browsing — so this is a harness concern, not a product one.

One spec at a time:

```bash
npx playwright test tests/03-ask-and-citations.spec.ts
npx playwright test --ui          # pick and step through
npx playwright show-report        # after a run
```

Each `describe` is `.serial` and shares state between its tests, so run whole spec
files. `-g` on a single test inside one will fail on the setup it skipped.

## What each file covers

| Spec | Covers |
|---|---|
| `01-shell.spec.ts` | the app loads, routes, the library matches the API, health is green, and the mock layer is **not** serving |
| `02-conversation-lifecycle.spec.ts` | **create, rename, delete** — from the sidebar menu and in place in the top bar, with cancel, blank-title and search paths |
| `03-ask-and-citations.spec.ts` | a scoped question, the cited answer, chip → popover → panel → highlighted span, `?doc=&page=` links, follow-up scope, abstention |
| `04-summary-existing-document.spec.ts` | picking an **existing** document from the scope menu and summarising it: nine fields, absences explicit, every chip resolving to real page text |
| `05-upload-and-summarise.spec.ts` | **uploading a new document** through the library, waiting for `ready`, then summarising it — asserted fact by fact against the fixture, plus the audit and cost rows |
| `06-draft-memo.spec.ts` | **drafting a memo**: the draft payload, the unreviewed banner, `[ATTORNEY INPUT REQUIRED]` markers, `.docx` and Markdown export, copy |
| `07-ui-surfaces.spec.ts` | ⌘K palette, ⌘B, ⌘N, Shift+Enter, empty-state suggestions, file-type rejection, stop mid-stream, matter switcher, document delete, settings, full-page viewer |
| `08-scanned-pdf-rejection.spec.ts` | FR-2: an image-only PDF is **rejected** with a readable reason, is not offerable as a source, and is refused with a 409 if asked for anyway |
| `09-two-tabs.spec.ts` | one user, two tabs — the smallest case that exercises the in-process pending-turn registry and the per-owner stop |

## Reading the output: `✘` is not always a failure

Five tests are marked `test.fail()`. Each one asserts the behaviour the product should
have and currently does not, with the defect named in a comment above it. Playwright
prints them with a `✘` glyph and then counts them in the **passed** total, because an
expected failure is a documented defect rather than a broken test — and if one ever
starts passing, Playwright fails the run and says so. **The summary line is the truth,
not the glyphs.**

That guard is how D2 and D5 were confirmed closed: both tests flipped to passing, the
run failed on the unexpected pass, and the marks came off.

They are marked this way for a second reason: a plain failure inside a `.serial`
describe aborts every test after it. One known bug was hiding seven working behaviours
in the specs below it.

| Test | Open defect |
|---|---|
| `TC-02.9` | deleting a conversation sends the user to `/chat/new` whatever they were looking at |
| `TC-02.10` | choosing Rename or Delete in a row menu navigates into that conversation — the cause of TC-02.9 |
| `TC-06.3c` | a long drafting request becomes a draft title cut off mid-word |
| `TC-07.15` | the settings page says the data is mocked while it is reading a real database |
| `TC-09.2` | Stop in one tab cancels every in-flight turn the user has, in every tab |

Fixed on 2026-10-22, and now pinned by ordinary passing tests: **D2** — an answer and a
drafted memo carry a citation chip against each claim (`TC-03.4`, `TC-06.3b`, and
`TC-03.4b`/`TC-03.5`, which had been skipping behind it); **D5** — a turn that produced
nothing says so rather than rendering blank (`TC-09.4`).

## Fixtures

Both are generated, text-layer-exact, and small enough to read.

- `fixtures/northwind-arbor-msa.pdf` — a three-page services agreement written so that
  eight of the nine summary fields are genuinely present and one is not. That is what
  makes "an absent field says so" a test rather than a tautology, and it is why
  `TC-05.4` can assert the fee, the term and the governing law by value.
- `fixtures/scanned-nda.pdf` — one page rendered to a bitmap: pixels, zero extractable
  characters. A scanner's output, for the FR-2 path.

## Findings kept alongside the tests

`findings/` holds a screenshot for every defect in
[`docs/e2e-test-report.md`](../docs/e2e-test-report.md), named for the defect it shows
(`D1-a…`, `D2…`, through `D7…`), plus `evidence-multipage-citation.txt` for D4's
measurements.

They are produced by a separate run, kept out of `tests/` so an ordinary `npm test`
never pays for it — D3 has to generate two live answers to catch one cancelling the
other:

```bash
npx playwright test --config=playwright.evidence.config.ts
```

Each case finds its own evidence: it either reproduces the defect live, or locates a
turn already exhibiting it and opens the screen that shows it. A case that can find
nothing fails rather than photographing something else.

## Notes on the data

The suite runs against the live database and leaves what it made behind, so a run can
be inspected in the browser afterwards. Conversations it creates are prefixed `E2E ·`
where it names them at all. Uploads of the two fixtures are cleared at the start of
the specs that upload them, because the library has no notion of "the same file again"
— a second upload is a second document with the same title, and the assertions would
go ambiguous on the second run.

## Why the waits are shaped this way

A turn is finished when the composer's **stop button goes away**: `useChat` shows it
for exactly as long as it holds a streaming turn, so the button appearing and then
disappearing is the same signal the user is watching. Polling for text, or waiting on
the SSE response, both report "done" at the wrong moment — the response promise
resolves on headers, long before the last token.
