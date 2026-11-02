# CI

One workflow, `ci.yml`, in `.github/workflows/` **at the repository root** — not
under `backend/`, where it was first written. Actions only reads workflows from
the root, so a workflow filed in a subdirectory never runs at all.

`ci.yml` is the gate on `main`: ruff and the unit suite on two interpreters, the
retrieval tests against real models, the production image built and booted, and
the frontend linted, typechecked and built — with a browsable HTML test report
and coverage report attached to every run.

There used to be a second workflow, `ci-retrieval.yml`, holding the retrieval
tests on the theory that they were too expensive to gate. See
[Why there is one workflow](#why-there-is-one-workflow).

No secrets. Nothing in CI makes a network call to the vendor, so there is no
`ANTHROPIC_API_KEY` in either workflow and nothing asks for one. That is a
constraint on what can be gated, not an oversight — see
[What is not gated](#what-is-not-gated).

## Scope: `backend/`, a subdirectory of the repository

The git root is `Legal Lens/`, so `frontend/`, `e2e/` and `intent-classifier/`
are all in the checkout. These two workflows nonetheless check `backend/` only:
every job sets `working-directory: backend` for its `run:` steps, and the paths
handed to actions — setup-uv's `cache-dependency-glob`, the Docker build
`context` and `upload-artifact`'s `path` — are spelled `backend/...` relative to
the root, because an action's own inputs ignore `working-directory`. The other three components have no CI yet; they need
their own jobs.

Two backend tests read across into the siblings — `test_conversation_titles.py`
checks `frontend/src/lib/constants.ts` and `test_orchestrator_intent.py` checks
`intent-classifier/intent_prompt.md`. They skip themselves when the folder is
absent, but it no longer is, so in CI they now run and assert. That is the
cross-component check those tests were written for, and it means a frontend
constant drifting from the backend's now fails the gate.

A prerequisite worth stating plainly: Actions runs on GitHub, so none of this
executes until the branch is pushed. The remote exists
(`github.com/hanoknaidu/legallens`); as of this writing the work is on
`feature/webcode`, and `ci.yml` triggers on PRs to and pushes to `main`, so
opening the PR is what first exercises it.

## `ci.yml` — the gate

| Job | What it does | Blocking |
|---|---|---|
| `quality` | `ruff check`, annotated onto the PR diff | yes |
| `quality` | `ruff format --check` | yes — see [Formatting](#formatting) |
| `test` (py3.12) | unit suite + coverage, the floor `pyproject.toml` declares | yes |
| `test` (py3.14) | unit suite + coverage, what `.venv` and the Dockerfile run | yes |
| `quality` | byte-compiles `intent-classifier/classify.py` | yes |
| `docker` | builds the default image and boots it — see [Docker](#docker) | yes |
| `frontend` | `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build`; uploads the bundle | yes |
| `e2e-specs` | `playwright test --list`, asserting 73 tests in 9 files | yes |
| `retrieval` | the 34 tests that load real models and a real vector store | yes |
| `ci` | one aggregate check to require in branch protection | yes |

The six run in parallel; `ci` waits on all of them. Each job sets its own
`working-directory` — `backend/`, `frontend/` or `e2e/` — because no component
sits at the repository root.

### What is not gated

Two things cannot run here, both for the same reason: they need a real
`ANTHROPIC_API_KEY`.

| Not gated | Why | What is checked instead |
|---|---|---|
| the e2e suite | needs a live backend, a built frontend and a key; cost $1.35 over 114 model calls when measured ([report](../docs/e2e-test-report.md)) | `--list` proves all 73 specs still load and collect |
| `intent-classifier/classify.py` | every invocation is a vendor call | `python -m compileall` proves it still parses |

A gate that needs a secret and spends money on every PR is a gate people turn
off. The real cross-component assertions are in the unit suite instead:
`test_conversation_titles.py` reads `frontend/src/lib/constants.ts` and
`test_orchestrator_intent.py` reads `intent-classifier/intent_prompt.md`, so
drift in either fails `test` without a key.

Running the e2e suite for real belongs in a scheduled or dispatched workflow
with a secret. It does not exist yet.

### The frontend job

`package.json` defines `dev`, `build`, `preview`, `typecheck` and `lint`. There
is still no unit-test script, so the job does not invent one. `typecheck` and
`build` overlap, since `build` is `tsc -b && vite build`, and both run anyway:
the separate typecheck reports a type error as a type error rather than as a
failed build, and the build catches what tsc cannot see, such as an unresolvable
import or a Vite plugin failure. `npm ci` is what gives the same lockfile
guarantee `--frozen` gives uv.

Node comes from `.nvmrc` at the repository root via `node-version-file`, so CI
and `nvm use` cannot drift. It is at the root rather than in `frontend/` because
`e2e/` is a Node project too, and nvm reads the nearest `.nvmrc` walking up from
the working directory — one file serves both.

#### ESLint, and the warning ratchet

Flat config in `frontend/eslint.config.js`: ESLint 10, typescript-eslint,
`react-hooks` and `react-refresh`. Type-aware linting is deliberately off —
`tsc -b --noEmit` already runs with `strict`, `noUnusedLocals`,
`noUnusedParameters` and `noFallthroughCasesInSwitch`, so reading the program
again in ESLint would re-report what tsc reports, several times slower. ESLint
covers what tsc does not look at: hook rules and fast-refresh boundaries.

The `lint` script is `eslint . --max-warnings 9`. Nine is the count the
repository had the day ESLint went in — **a ratchet, not a target**. A tenth
warning fails the job; working one of the nine off means lowering the number.

The nine, all pre-existing:

| Count | Rule | Where |
|---|---|---|
| 6 | `react-hooks/set-state-in-effect` | `useMediaQuery`, `PageToolbar`, `CommandPalette`, `TopBar`, `ChatPage` |
| 3 | `react-refresh/only-export-components` | `Highlight.tsx`, `IngestStatus.tsx` |

They are warnings rather than errors because fixing them is a behaviour change,
not a lint fix. `useMediaQuery` re-reads `matchMedia` inside its effect to cover
the query changing between render and effect — the rule objects to exactly the
line that exists to handle that case. The react-refresh three are helper
functions (`spanPattern`, `findSpan`, `STATUS_META`) exported beside a
component, which costs fast refresh in dev and nothing in production. Reworking
five components' state synchronisation, in a codebase with no frontend unit
tests, is its own task with its own review.

### Why there is one workflow

The retrieval tests — the 34 in `test_orchestrator_retrieval.py` and
`test_orchestrated_retrieval.py` — lived in their own workflow,
`ci-retrieval.yml`, path-filtered and outside the required check. The reasoning
was cost: the `workflows` extra pulls torch, the CUDA runtime and triton, and
the suite was expected to download ~4.5GB of weights on its first question.

Measured, it does not. The first run ever, on a cold cache with nothing to
restore from, took **1m35s wall clock with 34 tests passing in 23.73s**. So the
split was buying nothing and costing something real: a job outside the aggregate
can go red without stopping a merge, and a path-filtered job skips most PRs
entirely. It is a job in `ci.yml` now, and it blocks.

Its `timeout-minutes` came down from 60 to 30 in the move. Sixty was harmless
for a job that gated nothing; now that the aggregate waits on it, the timeout is
how long a hang can hold a PR.

The daily `schedule` trigger came along with it and now applies to the whole
gate, which is a superset of what it was for. Note that a scheduled run only
fires from the default branch, and GitHub disables the schedule after 60 days of
repository inactivity.

### Triggers

```yaml
pull_request: [main]   # the one that guards a merge
push:         [main]   # re-runs on the commit that landed
workflow_dispatch      # on demand
```

`pull_request` is what actually gates a merge, and GitHub runs it against the
*merge result* rather than the branch tip — so it catches the change that only
breaks once combined with `main`. The `push` entry re-runs the same checks on
the commit that landed, which is the signal a CD pipeline reads later.

Runs are cancelled by a newer push to the same ref (`concurrency`), which is
per-PR and so never cancels a run on `main`. The token is `contents: read`; CD
will need more and should ask for it in its own workflow rather than widening
this one.

### Require this check

Point branch protection at **`CI`**, the aggregate job — not at
`Tests (py3.12)`. A matrix job's name carries its matrix value, so requiring
those by name breaks the moment the matrix changes. `CI` also fails on a
*skipped* or *cancelled* dependency, which `needs:` alone would treat as
satisfied and let through as a green merge.

## Running the same thing locally

The commands in the workflow are the commands you run by hand. Copy them out
verbatim; they are written literally rather than assembled from a variable so
they work in zsh as well as in Actions' bash.

```bash
cd backend

# the two quality checks, as the gate runs them
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .

# the suite, as the gate runs it
uv run --frozen --extra dev --extra ocr --extra orchestrator \
  pytest --cov \
         --cov-report=html:reports/coverage \
         --html=reports/pytest-report.html --self-contained-html \
         -q -rs

open reports/coverage/index.html
open reports/pytest-report.html
```

`--frozen` makes uv use `uv.lock` exactly and fail if `pyproject.toml` has
drifted from it, which turns "edited the manifest without relocking" into a
failure instead of a silent re-resolve. `reports/` is gitignored.

### Which extras, and why those three

`dev` alone does not work. The heavy imports are lazy — `import pymupdf` and
`import langchain_anthropic` sit *inside* functions — so they are invisible at
collection and surface as runtime failures instead of skips:

| Extras | Result |
|---|---|
| `dev` | **18 failed, 11 errors**, 307 passed |
| `dev ocr orchestrator` | 336 passed, 1 skipped — ~8s |
| `+ workflows` | 358 passed — adds torch, several GB |

`dev ocr orchestrator` is the lean sufficient set and is what `ci.yml` installs.
`workflows` is deliberately absent from the gate; the tests behind it
`importorskip` themselves, so the suite stays green and honest about skipping
them.

### Expected result

**336 passed, 1 skipped.** The one skip is structural, not a failure:

| Skip | Why |
|---|---|
| `test_orchestrator_retrieval.py` | `workflows` extra absent by design — the `retrieval` job owns them |

`-rs` prints the reason into the log, so a *new* skip shows up rather than being
quietly absorbed into the count.

This figure changed when the workflows moved to the repository root: the two
sibling-reading tests used to skip in CI and brought the expected result to 334
passed / 3 skipped. They now run, as they always have locally.

With the `workflows` extra installed — which your `.venv` has — you get **358
passed, 0 skipped** locally, because the retrieval tests import rather than
skip. A clean checkout has no `.env`, which is why `Settings`' `env_file=".env"`
reads nothing in CI and the defaults apply.

## Reports

Every run uploads an artifact per interpreter, `ci-reports-py3.12` and
`ci-reports-py3.14`, and does so **on failure too** — a red suite is exactly
when the report is worth reading.

| File | Size | What it is |
|---|---|---|
| `pytest-report.html` | ~1.1MB | every test, pass/fail, timing. One self-contained file |
| `coverage/index.html` | 56KB, 82 files in the dir | line-by-line, branch coverage |
| `coverage.xml` | ~189KB | Cobertura, for a badge or a CD gate later |
| `junit.xml` | ~43KB | for a test-reporting action later |

The coverage total and a per-file table are also written to the run summary, so
the number is visible without downloading anything.

Coverage is **83%**, measured and reported but **not gated** — there is no
`fail_under`. Much of what is uncovered is the lazy optional-extra guards that
cannot run in the gate's configuration. Add a floor once the number settles.

## Formatting

Both ruff checks block. The repository had been linted but never formatted, so
it was put through one mechanical pass when this pipeline was added — **52 files
reformatted, 63 already clean**, with the suite unchanged at 358 passed. The tree
is clean as of that commit, so any formatting diff from here is a real one rather
than a share of that backlog.

If the step fails:

```bash
uv run ruff format .
```

The two ruff checks cannot fight each other: the configured lint rules
(`E`, `F`, `I`, `UP`, `B`, `ASYNC` in `pyproject.toml`) include no stylistic rule
the formatter also owns.

## Docker

The `docker` job builds the default image and then **boots it**, because a build
that succeeds and an image that runs are different claims — the gap between them
is where a bad `ENTRYPOINT` or a missing runtime file lives. The unit suite
imports from `src/` and never exercises the Dockerfile at all, so without this
job the gate can go green on a change that breaks the thing that ships.

It builds `EXTRAS=""` only: that is the configuration that boots with no local
model and no vector server. The `workflows` build is 6.42GB
([DOCKER.md](DOCKER.md)) and belongs nowhere near a PR gate.

The smoke test needs no secret, which is the whole reason it can live in the
gate:

```bash
docker run -d --rm -p 8000:8000 \
  -e LEGAL_LENS_LLM_PROVIDER=echo \
  -e LEGAL_LENS_ENVIRONMENT=test \
  legallens-backend:ci
curl -fsS http://127.0.0.1:8000/api/health
```

`echo` needs no key at all and `environment=test` skips the startup probe that
would otherwise make one vendor call. It then asserts on `"status":"ok"` in the
body rather than on curl's exit code, since a degraded check still returns 200.
Verified against this image: healthy in 2s, every check `ok`. The health JSON is
echoed into the run summary.

Layers are cached via buildx's GitHub Actions cache backend
(`cache-from/to: type=gha`), which is why `docker/setup-buildx-action` is there —
the default docker driver cannot use it.

The job **never pushes.** Publishing is CD's job and needs a registry credential
this workflow deliberately does not hold.

## The `retrieval` job

`test_orchestrator_retrieval.py` and `test_orchestrated_retrieval.py` are the
only files that load actual models and run an actual vector store — 34 tests.
They need the `workflows` extra, which pulls torch, the CUDA runtime and triton,
so the other three Python jobs leave it out and those tests `importorskip`
themselves there. That skip is the one the `test` job reports.

The model weights (`BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`, `Qdrant/bm25`) are
cached with `actions/cache` under a key named for the models rather than for the
lockfile, since they change when `DEFAULT_EMBED_MODEL` / `DEFAULT_RERANK_MODEL`
change, not when a wheel does. `restore-keys` lets a run with a changed key
start from the last download rather than from nothing.

It runs on 3.14 only — the point is the retrieval stack, not interpreter
coverage, and a matrix would double a multi-GB install. And no coverage: it runs
two files, so its percentage would read as a project total while measuring a
slice of it. The `test` job owns the coverage number.

For why this is a job here rather than its own workflow, see
[Why there is one workflow](#why-there-is-one-workflow).

## Pinned versions

`uv` is pinned to **0.12.23**, the version that wrote `uv.lock` and the one the
Dockerfile copies (`ghcr.io/astral-sh/uv:0.12.23`). A newer uv can resolve or
rewrite the lockfile differently, which would let CI pass against a dependency
set the production image never builds. Bump it here and in the Dockerfile
together.

Actions are pinned to major tags — `actions/checkout@v7`,
`actions/upload-artifact@v7`, `actions/cache@v6`, `actions/setup-node@v7`,
`docker/setup-buildx-action@v4`, `docker/build-push-action@v7` — with one
exception. **`astral-sh/setup-uv` is pinned to the full `v10.3.3`**, because
astral-sh stopped publishing the floating major alias after `v7`: `@v10` matches
no ref, and a job that asks for it fails at "Set up job" before a step runs.
That is not hypothetical — it is how the first run of this pipeline failed. When
bumping it, check that the tag you pick actually exists.

Node comes from `.nvmrc` at the repository root rather than a pin here.

## Not covered yet

- **CD** — nothing here tags or pushes an image. The `docker` job builds and
  boots one, then throws it away; the `push: [main]` trigger is the hook a deploy
  workflow reads.
- **Type checking** — no mypy or ty is configured in `pyproject.toml`, and
  adding one as a blocking gate to a codebase that has never been checked would
  behave like the formatting problem above.
- **`frontend/`, `e2e/`, `intent-classifier/`** — outside this repository; see
  [Scope](#scope-this-repository-is-backend).
