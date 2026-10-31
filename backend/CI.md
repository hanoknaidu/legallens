# CI

Two workflows, both in `.github/workflows/`. `ci.yml` is the gate on `main`:
ruff plus the unit suite on two interpreters, with a browsable HTML test report
and coverage report attached to every run. `ci-retrieval.yml` carries the 21
tests that load real models, which are too expensive to put on every PR.

No secrets. Nothing in CI makes a network call to the vendor, so there is no
`ANTHROPIC_API_KEY` in either workflow and nothing asks for one.

## Scope: this repository is `backend/`

The git root is `backend/`, not `Legal Lens/`. The sibling `frontend/`, `e2e/`
and `intent-classifier/` folders are outside it, so CI does not see them and
cannot check them. Two tests look for those folders and skip themselves when
they are absent, which in CI is always — see [Expected result](#expected-result).

A prerequisite worth stating plainly: Actions runs on GitHub, so none of this
executes until the repository has a GitHub remote and the branch is pushed.

## `ci.yml` — the gate

| Job | What it does | Blocking |
|---|---|---|
| `quality` | `ruff check`, annotated onto the PR diff | yes |
| `quality` | `ruff format --check` | yes — see [Formatting](#formatting) |
| `test` (py3.12) | unit suite + coverage, the floor `pyproject.toml` declares | yes |
| `test` (py3.14) | unit suite + coverage, what `.venv` and the Dockerfile run | yes |
| `docker` | builds the default image and boots it — see [Docker](#docker) | yes |
| `ci` | one aggregate check to require in branch protection | yes |

`quality`, `test` and `docker` run in parallel; `ci` waits on all three.

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

**334 passed, 3 skipped.** All three skips are structural, not failures:

| Skip | Why |
|---|---|
| `test_orchestrator_retrieval.py` | `workflows` extra absent by design — `ci-retrieval.yml` owns it |
| `test_conversation_titles.py` | `frontend/` is a sibling outside this repository |
| `test_orchestrator_intent.py` | `intent-classifier/` is a sibling outside this repository |

`-rs` prints each reason into the log, so a *new* skip shows up rather than
being quietly absorbed into the count.

Locally you will see 336 passed / 1 skipped instead: the two sibling folders
exist on your disk, so those tests run. A clean checkout has neither them nor a
`.env`, which is why `Settings`' `env_file=".env"` reads nothing in CI and the
defaults apply.

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

Coverage is **79%**, measured and reported but **not gated** — there is no
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

## `ci-retrieval.yml` — the retrieval stack

`tests/test_orchestrator_retrieval.py` is the one file that loads actual models
and runs an actual vector store. The `workflows` extra pulls torch, the CUDA
runtime and triton — several GB a CPU runner never executes — and the suite then
downloads ~4.5GB of weights (`BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`,
`Qdrant/bm25`). See the measured image sizes in [DOCKER.md](DOCKER.md).

So it is split out, with three triggers that keep it genuinely gated rather than
merely scheduled:

- **`pull_request`, path-filtered** — a PR touching `orchestrator/`,
  `retrieval/`, either retrieval test, `pyproject.toml` or `uv.lock` does get
  checked before merge
- **`schedule`**, 07:00 UTC daily — a break from a dependency bump surfaces
  within a day. Note that scheduled workflows run only from the default branch,
  and GitHub disables them after 60 days of repository inactivity
- **`workflow_dispatch`** — before merging something risky

The weights are cached (`actions/cache`) under a key named for the models rather
than for the lockfile, since they change when `DEFAULT_EMBED_MODEL` /
`DEFAULT_RERANK_MODEL` change, not when a wheel does. A cold run is slow enough
to justify `timeout-minutes: 60`; a warm one is a few minutes. It runs on 3.14
only — the point is the retrieval stack, not interpreter coverage, and a matrix
would double a multi-GB download.

No coverage here: it runs two files, so its percentage would read as a project
total while measuring a slice of it. `ci.yml` owns the coverage number.

## Pinned versions

`uv` is pinned to **0.12.23**, the version that wrote `uv.lock` and the one the
Dockerfile copies (`ghcr.io/astral-sh/uv:0.12.23`). A newer uv can resolve or
rewrite the lockfile differently, which would let CI pass against a dependency
set the production image never builds. Bump it here and in the Dockerfile
together.

Actions are pinned to major tags: `actions/checkout@v7`,
`astral-sh/setup-uv@v10`, `actions/upload-artifact@v7`, `actions/cache@v6`,
`docker/setup-buildx-action@v4`, `docker/build-push-action@v7`.

## Not covered yet

- **CD** — nothing here tags or pushes an image. The `docker` job builds and
  boots one, then throws it away; the `push: [main]` trigger is the hook a deploy
  workflow reads.
- **Type checking** — no mypy or ty is configured in `pyproject.toml`, and
  adding one as a blocking gate to a codebase that has never been checked would
  behave like the formatting problem above.
- **`frontend/`, `e2e/`, `intent-classifier/`** — outside this repository; see
  [Scope](#scope-this-repository-is-backend).
