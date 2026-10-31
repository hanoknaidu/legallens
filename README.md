# Legal Lens

Ask, summarise and draft from your own legal documents — with every claim traceable to
a page.

A React frontend and a FastAPI backend. Upload contracts, ask questions about them,
and get answers where each assertion carries a citation to the page it came from. When
the documents do not answer a question, it says so instead of inferring one.

---

## Quick start

Two terminals. The frontend talks to the backend through a Vite proxy, so both run on
localhost with no CORS setup.

```bash
# terminal 1 — backend
cd backend
uv sync --extra dev
uv run uvicorn legal_lens.main:app --reload --port 8000

# terminal 2 — frontend
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The database starts empty — upload a PDF, DOCX or TXT
and wait for it to reach `ready`, then ask a question.

No API key is needed to start. The default generation provider is `echo`, which
answers by quoting the real documents rather than calling a model. It is a supported
configuration, not a stub: the whole pipeline — retrieval, citations, streaming,
abstention, export — runs on it offline and free.

### Without a backend at all

```bash
cd frontend
echo 'VITE_USE_MOCKS=true' > .env.development.local
npm run dev
```

That serves the app from its MSW mock layer, which is also the only path with demo
content (three documents and five conversations). Useful for frontend work and for a
demo on a plane.

---

## Running with a real model

Generation, OCR and the intent classifier each need an Anthropic API key.

```bash
cd backend
python3 set_key.py        # prompts without echoing; writes .env line 7
python3 check_key.py      # confirms it is present, authenticates, and has credit
```

`check_key.py` checks those three things separately because they fail for different
reasons and have different fixes. Two in particular are easy to hit:

- **API credits are separate from a Claude.ai subscription.** A Pro or Max plan grants
  no API quota — add credits at
  [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).
- **A key that is not scoped to a workspace** authenticates and then fails every
  request. Either create the key inside a workspace, or set
  `ANTHROPIC_WORKSPACE_ID=wrkspc_…` in `backend/.env`.

Then:

```bash
LEGAL_LENS_LLM_PROVIDER=anthropic uv run uvicorn legal_lens.main:app --reload --port 8000
```

At startup the service makes one one-token request to verify the key and reports the
result on `GET /api/health`. **Check it before assuming generation works:**

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
```

`llm_provider: "ok (claude-opus-5-5)"` means a real call succeeded. Anything else names
the problem — `api key rejected`, `no api credit`, `needs a workspace id`.

> `.env` is gitignored and `.env.example` is not. Put real keys only in `.env`;
> `set_key.py` does that for you.

---

## Running the orchestration layer

`llm_provider="anthropic"` sends one prompt per intent over pages the service
selected. `llm_provider="orchestrator"` instead runs the three workflows in
[`orchestrator/`](backend/src/legal_lens/orchestrator/): intent routing, a query
rewrite, hybrid retrieval with a reranker, and a different model and effort per
workflow.

```bash
cd backend
uv sync --extra dev --extra workflows   # torch, Qdrant, fastembed: minutes, ~2GB
LEGAL_LENS_LLM_PROVIDER=orchestrator \
LEGAL_LENS_INTENT_ROUTER=model \
  uv run uvicorn legal_lens.main:app --reload --port 8000
```

**The first question is slow and the rest are not.** Two local models load on first
use — `bge-m3` to embed and `bge-reranker-v2-m3` to rerank, about 2.5GB resident. They
load in a worker thread, so nothing else in the service stalls behind them, and `warm`
appears in the log when they are ready.

Everything except storage is configured on the `ORCH_` variables the CLI reads, so a
setting tuned against the eval corpus means the same thing here. The useful ones:

| Variable | Default | Notes |
|---|---|---|
| `ORCH_RERANK_ENABLED` | `1` | `0` turns the cross-encoder off. The measurements are on `OrchestratorConfig.rerank_enabled` — read them before re-enabling or disabling it |
| `ORCH_ASK_TOP_N` | `8` | pages reaching an `ask` prompt |
| `ORCH_DRAFT_TOP_N` | `12` | wider, because a memo cites more ground than an answer |
| `ORCH_RERANK_MIN_SCORE` | *(no floor)* | **leave unset** unless you have looked at real scores from the reranker in use — a floor on a logit-output reranker discards every candidate and presents as an assistant that abstains on answerable questions |
| `ORCH_DEVICE` | auto | `mps` on Apple silicon, `cpu` elsewhere |

### What this path does differently, and why it matters

**It retrieves for itself.** `TurnRunner` skips selection and hands the provider a
document scope. That is not a shortcut: whether an absence is provable depends on
whether every page in scope was read, and the prompt is built from that flag
(`Retrieved.coverage`). Under partial coverage the model is forbidden from saying the
documents are silent, and its abstention is worded *"the excerpts retrieved do not
address this"* rather than *"the documents do not"*. Only whoever chose the pages can
know which case it is in, so choosing and concluding are one decision.

**A turn can cost two models.** A follow-up question is rewritten by Haiku before
Sonnet answers it. The audit row totals the turn and names the model that wrote the
answer; `usage_costs` carries one row per call, so "what is the rewrite step costing
us?" is answerable from the table rather than from new instrumentation.

**Its index is separate from the CLI's.** The service writes to
`{data_dir}/qdrant-live`, the CLI to `./var/qdrant-orchestrator`. Embedded Qdrant takes
an exclusive directory lock, so sharing one would mean the CLI could not run while the
service was up — and, if the service won the race, that it would answer questions from
eval fixtures.

---

## Deployment constraint: one worker

**Do not add `--workers`.** Two parts of the service are in-process by design:

- the **pending-turn registry**, which holds an assistant turn between
  `POST /chat/message` and the `GET /chat/stream` that generates it. A second worker
  would not have the entry, and streams would 404 at random.
- **SQLite**, which takes a single writer. A pool trades a predictable bottleneck for
  intermittent `database is locked`.
- **embedded Qdrant** under `llm_provider="orchestrator"`, which takes an exclusive
  lock on its directory. A second worker does not get a degraded index; it fails to
  open one at all.

Rate limiting is also per-process. At the 20 concurrent users this is designed for
(NFR-4), multi-worker buys nothing; making it possible means Redis for the registry
and Postgres or a write queue for the database, and that is one change, not an
incremental tuning step.

---

## Configuration

Everything is environment variables, prefixed `LEGAL_LENS_`, readable from
`backend/.env`. Defaults are in
[`config.py`](backend/src/legal_lens/config.py). The ones that matter:

| Variable | Default | Notes |
|---|---|---|
| `LEGAL_LENS_LLM_PROVIDER` | `anthropic` | `echo` runs offline with no key; `orchestrator` is the three-workflow pipeline |
| `LEGAL_LENS_INTENT_ROUTER` | `regex` | `model` routes `ask`/`summary`/`draft` through Haiku |
| `ANTHROPIC_API_KEY` | — | also accepted as `LEGAL_LENS_ANTHROPIC_API_KEY`, which wins |
| `ANTHROPIC_WORKSPACE_ID` | — | only for a key not scoped to a workspace |
| `LEGAL_LENS_GENERATION_MODEL` | `claude-opus-5-5` | |
| `LEGAL_LENS_ROUTER_MODEL` | `claude-haiku-4-5` | intent classification |
| `LEGAL_LENS_WHOLE_DOCUMENT_TOKEN_BUDGET` | `600000` | below this the whole corpus goes into context instead of retrieval |
| `LEGAL_LENS_OCR_ENABLED` | `false` | see below |
| `LEGAL_LENS_RATE_LIMIT_PER_MINUTE` | `60` | `0` disables; `/health`, `/metrics` and the answer stream are exempt |
| `LEGAL_LENS_PROBE_LLM_ON_STARTUP` | `true` | the one-token key check |

---

## OCR for scanned PDFs — off by default, deliberately

A scanned PDF is **rejected**, not transcribed. FR-2 of the requirements is explicit
about why: *"the silent-failure mode is worse than a clear rejection."* A scan that
indexes as empty text produces confident answers about nothing.

OCR exists and can be switched on:

```bash
uv sync --extra ocr
LEGAL_LENS_OCR_ENABLED=true uv run uvicorn legal_lens.main:app --reload --port 8000
```

Two engines. `claude` (default) needs the API key and no system binary; `tesseract`
makes no vendor call but needs `brew install tesseract`.

**Understand the trade before enabling it.** A citation into OCR'd text quotes what
the OCR engine produced, not what the document provably says. A vision model asked to
read a blurry indemnity clause can emit well-formed contract language that was never
on the page — confident, quotable and wrong. Four things guard against that:

- the prompt demands `[ILLEGIBLE]` over guessing, and `[NO TEXT]` for a page with
  nothing on it;
- a page the model **described** instead of transcribing is stored as empty rather
  than as its prose. This is not hypothetical: shown a blank page, Haiku returned
  *"The image provided appears to be completely blank…"*, which is 145 characters of
  commentary with no `[ILLEGIBLE]` marker in it — so it passed the empty check and the
  illegible-ratio check and indexed as the document's own words. The prompt forbids it
  in two separate rules and the model did it anyway, so `transcription_of()` enforces
  it in code. An all-described scan is now rejected, not indexed;
- documents more than 25% illegible are rejected;
- every OCR'd document carries `textSource: "ocr"` on the wire, so the provenance
  travels with it.

Transcription is costed like any other model call — one `usage_costs` row per
document, with `purpose = "ocr"` — and it is billed before the verdict, because a scan
rejected for illegibility still cost a vendor call per page.

---

## Layout

```
backend/
  src/legal_lens/
    main.py              app factory, lifespan, middleware, router mount
    config.py            every setting
    api/                 one module per resource
    ai/                  generation providers, pricing, the startup probe
    chat/                pending-turn registry, the turn runner
    retrieval/           embeddings, vector store, whole-document vs RAG
    ingestion/           parse, chunk, OCR
    exporting/           .docx and .md, with the FR-17 notice inside the file
    orchestrator/        the three workflows, hybrid retrieval, intent — standalone
    ai/orchestrator_provider.py   those workflows behind the generation seam
    ai/adapt.py                   the one translation between the two type universes
    retrieval/orchestrated.py     the orchestrator's index, fed from the database
    db/                  schema.sql, session, repositories
  tests/                 331 tests; eval/ holds the retrieval evaluation set
frontend/
  src/
    lib/api.ts           the only place that knows the data might be mocked
    lib/sse.ts           the streaming protocol
    mocks/               MSW handlers — the contract fixture, do not delete
docs/                    requirements, and the integration and workflow plans
```

---

## Development

```bash
cd backend
uv run --extra dev pytest -q          # 331 tests
uv run --with ruff ruff check src/ tests/
uv run python tests/eval/validate.py  # check the eval set is self-consistent

cd frontend
npm run typecheck
npm run build
```

The intent classifier runs standalone, with no backend, database or frontend:

```bash
cd backend
uv run --extra orchestrator python -m legal_lens.orchestrator.cli --suite
uv run --extra orchestrator python -m legal_lens.orchestrator.cli "draft a memo on venue"
```

---

## Things worth knowing before changing anything

**Citations are resolved by position, not by asking the model.** Each document is sent
as a custom-content document whose content blocks are its pages, so a citation returns
a `start_block_index` that maps to a page. Asking a model to write page numbers is the
usual way this feature cites the wrong page.

**Cited text must appear verbatim on its page.** The viewer highlights by substring
search. Any normalisation during ingestion — collapsing whitespace, straightening
quotes — produces a chip that opens the right page and highlights nothing. Tests
assert this end to end.

**Absence is an answer.** "The contract is silent on insurance" is only provable if
the whole contract was read, which is why corpora under
`whole_document_token_budget` skip retrieval entirely. An abstention is a correct
outcome delivered over a 200, not an error.

**The nine summary fields are fixed, and an absent field is `null`, never omitted.** A
reader cannot otherwise distinguish a missing clause from a missing answer.

**The mock layer is the contract.** `frontend/src/mocks/` is what the backend was
built against and what its tests compare behaviour to. Keep it in step.
