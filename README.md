# Legal Lens

Ask questions of your own legal documents and get answers that cite the page they
came from — or an explicit "the documents do not address this" when they don't.

A general chatbot is unusable for legal work for one specific reason: it will
confidently invent a citation. So the constraint this whole codebase is built
around is not *answer well*, it is **answer only from the documents provided, and
show exactly where each answer came from**. Three features follow from that:

| | What the user does | What they get |
|---|---|---|
| **Ask** | uploads documents, types a question | an answer composed only of what the documents say, each assertion carrying a clickable citation to a page — or an abstention |
| **Summary** | picks one document | a fixed nine-field structured overview; each populated field cites its page, each expected-but-absent clause says so |
| **Draft** | asks for a memo or a clause | editable prose marked unreviewed, with `[ATTORNEY INPUT REQUIRED]` where it cannot know |

> **Course project.** Engineered to production standards so the engineering is
> instructive. It is not deployed to real attorneys or real client matters, and
> §8.3 of the spec is explicit that it is a tool for licensed professionals rather
> than a legal-advice service.

Full specification: [`docs/legal-lens-brd-prd-frd.md`](docs/legal-lens-brd-prd-frd.md).

## What is in here

```
backend/            FastAPI + SQLite + the ingestion, retrieval and orchestration layers
frontend/           React + Vite SPA; nginx serves it in a container and proxies /api
e2e/                Playwright specs driving the real UI against the real backend
intent-classifier/  the intent router on its own, runnable with nothing else installed
k8s/                the three manifests CD applies
.github/workflows/  ci.yml (the gate) and cd.yml (build, push, deploy to AKS)
docs/               the spec, the auth design, the e2e report, the plans
```

Everything is a subdirectory of the git root — no component sits at the top level,
which is why every CI job sets its own `working-directory`.

## Running it

### Both services, one command

```bash
cp backend/.env.docker.example backend/.env.docker   # then fill in ANTHROPIC_API_KEY
docker compose up --build
open http://localhost:8080
```

The browser only ever talks to the frontend; nginx inside it proxies `/api` to the
backend over Compose's private network, so the app is same-origin and CORS never
comes up. The stack defaults to `LEGAL_LENS_LLM_PROVIDER=echo`, a deterministic stub
that needs no key at all — switch to `anthropic` once the key is in place. Details,
image sizes and every environment variable: [`backend/DOCKER.md`](backend/DOCKER.md)
and [`frontend/DOCKER.md`](frontend/DOCKER.md).

### For development

Two terminals.

```bash
# backend
cd backend
cp .env.example .env                 # then fill in ANTHROPIC_API_KEY
uv sync --extra dev --extra ocr --extra orchestrator
LEGAL_LENS_LLM_PROVIDER=anthropic uv run uvicorn legal_lens.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev      # http://localhost:5173
```

Vite proxies `/api` to port 8000, so dev is same-origin too. `VITE_USE_MOCKS=true`
serves the MSW mock layer instead and needs no backend — it is also the only path
with demo content, since a real database starts empty.

The tests:

```bash
cd backend && uv run --frozen --extra dev --extra ocr --extra orchestrator pytest -q -rs
cd frontend && npm run lint && npm run typecheck
cd e2e && npm test                   # needs both servers up and a real key
```

## The choices worth knowing before you read the code

Every provider in this system is a seam selected by a setting and resolved through a
factory, because NFR-11 requires the model provider, the embedding provider and the
vector store to be swappable without touching an API contract or the frontend. The
defaults are the configuration that runs with the least installed, not the best one:

| Setting | Default | The other end of the seam |
|---|---|---|
| `LEGAL_LENS_LLM_PROVIDER` | `anthropic` | `echo` (no key, deterministic) · `orchestrator` (the three-workflow pipeline) |
| `LEGAL_LENS_INTENT_ROUTER` | `regex` | `model` — the Haiku classifier; the regex keys on words rather than meaning |
| `LEGAL_LENS_EMBEDDING_PROVIDER` | `hashing` | `local` (BAAI/bge-m3) · `voyage`. **`hashing` is a dependency-free stand-in, not a semantic embedder** |
| `LEGAL_LENS_VECTOR_STORE` | `memory` | `qdrant` |
| `LEGAL_LENS_OCR_ENABLED` | `false` | FR-2 requires a scanned PDF to be *rejected*; turning OCR on trades a clear rejection for text whose provenance is a transcription |

Two consequences of those defaults that have bitten before:

- **The default deployment runs real generation over weak retrieval.** `anthropic` +
  `hashing` + `memory` answers fluently; what suffers is which pages get found, which
  looks like the model being bad. The full stack — `orchestrator`, bge-m3, a real
  reranker — is one variable in a deployment: `BACKEND_EXTRAS="orchestrator workflows"`.
  It costs a 6.42GB image and ~4.5GB of model weights on the first question
  ([`backend/DOCKER.md`](backend/DOCKER.md), and the nodepool note in
  [`CD_readme.md`](CD_readme.md#switching-on-the-full-retrieval-stack)).
- **The backend is single-writer.** SQLite takes one writer and the pending-turn
  registry lives in memory, so `WORKERS` stays 1, there is one replica, and the
  deployment strategy is `Recreate`. Its data is on a disk, not in a database
  service: no volume means every uploaded document disappears with the pod.

### There is no sign-in

Authentication is built, tested and **off**. The login page was demonstrated and then
removed, so the app opens straight into the chat and no route checks a cookie; every
request is attributed to a single owner. `LEGAL_LENS_REQUIRE_AUTH=true` restores
scrypt hashing, server-side sessions, throttling and per-user isolation at the API —
but the SPA has no screen to acquire a session through, so turning it on for the
browser means restoring a login page first. The service warns at every boot and says
so on `/api/health`. The whole picture, including what was removed and where it lives
in git history: [`docs/auth.md`](docs/auth.md).

## CI and deployment

`ci.yml` gates `main`: ruff, the unit suite on two interpreters, the retrieval tests
against real models, the production image built *and booted*, the frontend linted,
typechecked and built, and the e2e specs collected. It holds no secrets and makes no
vendor call, which is a constraint on what can be gated rather than an oversight —
[`backend/CI.md`](backend/CI.md) spells out which checks that rules out and what
stands in for them. Require the aggregate **`CI`** check in branch protection, not a
matrix job by name.

`cd.yml` fires on CI succeeding on `main`, rebuilds both images from that commit,
tags them with the git SHA (never `latest`), pushes to ACR and rolls out to AKS,
rolling back a failed rollout. Merging is a person's job: a push made with the
`GITHUB_TOKEN` raises no event, so an auto-merged commit would land with no CI run of
its own and no deploy. The resource plan, the identity federation and the current
state of the wiring are in [`CD_readme.md`](CD_readme.md).

## Reading further

| | |
|---|---|
| [`docs/legal-lens-brd-prd-frd.md`](docs/legal-lens-brd-prd-frd.md) | the specification — why, what, and what the system must do |
| [`docs/workflows-plan.md`](docs/workflows-plan.md) | ask · summary · draft, and what the build changed about the plan |
| [`docs/auth.md`](docs/auth.md) | the authentication that exists but is not enforced |
| [`docs/e2e-test-report.md`](docs/e2e-test-report.md) | measured behaviour, cost per run, and the open defects |
| [`e2e/README.md`](e2e/README.md) | what each spec covers, and why a `✘` is not always a failure |
| [`backend/CI.md`](backend/CI.md) · [`CD_readme.md`](CD_readme.md) | the pipelines |
| [`backend/DOCKER.md`](backend/DOCKER.md) · [`frontend/DOCKER.md`](frontend/DOCKER.md) | the images |
| [`intent-classifier/README.md`](intent-classifier/README.md) | the intent router, standalone |
| [`ideas.md`](ideas.md) | the pre-architecture brain dump the spec was written from |
