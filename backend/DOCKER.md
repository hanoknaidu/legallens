# Running the backend in Docker

Two-stage build: `uv` installs the locked dependency set into a virtualenv, the
runtime stage copies that virtualenv onto a clean `python:3.14.10-slim-trixie`
and runs uvicorn as uid 10001. No compiler, no `uv`, no lockfile and no secret
ends up in the shipped image.

**221MB** on arm64, **195MB** on amd64, of which ~75MB is the virtualenv.

## Build

```bash
cd backend
docker build -t legallens-backend:local .
docker build --platform linux/amd64 -t legallens-backend:local-amd64 .   # for AKS
```

### Optional dependency sets

`--build-arg EXTRAS="..."`, space-separated, from
`[project.optional-dependencies]`. Empty by default, which is the configuration
that boots with no local model and no vector server.

| `EXTRAS` | Adds | Cost |
|---|---|---|
| *(empty)* | — | 195MB. `llm_provider=anthropic`, `embedding_provider=hashing`, `vector_store=memory` |
| `ocr` | PyMuPDF, pytesseract, pillow | ~+100MB. Needed before `LEGAL_LENS_OCR_ENABLED=true` does anything |
| `orchestrator` | langchain-anthropic, langchain-core | +44MB (verified: 265MB) |
| `orchestrator workflows` | torch, qdrant-client, fastembed, sentence-transformers | **6.42GB** (measured, amd64) — read the next section first |

## The models question

The default image downloads no models, at build time or at run time. The
`anthropic` provider sends text to the vendor; the `hashing` embedder is
deterministic and dependency-free. Nothing is fetched at startup except the
one-token key probe.

`llm_provider=orchestrator` with the `workflows` extra is the exception. It
loads two models on its **first question** — `BAAI/bge-m3` and
`BAAI/bge-reranker-v2-m3` (`orchestrator/config.py`), roughly 4.5GB together
and ~2.5GB resident — from HuggingFace, into `HF_HOME`.

Two further costs are specific to running it in a container, and neither shows
up on a Mac:

- **torch on linux pulls CUDA.** Measured on the built amd64 image: of a 6.0GB
  virtualenv, `nvidia/` is 3.2GB, `triton/` 897MB and `cuda*/` another 27MB —
  **4.1GB of GPU-only code** that a CPU AKS node will never execute. `uv.lock`
  resolves `cuda-toolkit`, `cuda-bindings`, `triton` and sixteen `nvidia-*`
  packages under `sys_platform == 'linux'`; torch itself is a further 1.2GB.
  If this path matters in production, pin the CPU-only torch build with a
  `[tool.uv.sources]` index entry rather than shipping a 6.42GB image.
- **A pod's filesystem is ephemeral.** With `HF_HOME` unset the 4.5GB download
  repeats on every pod start, not just the first ever.

**Recommendation: don't bake the models in — put `HF_HOME` on the data volume.**

```
HF_HOME=/data/hf
```

That downloads them once per volume rather than once per pod, keeps the image
small enough to pull quickly, and leaves the version pinned by whatever is in
the cache. Baking them in is the right call only if the cluster has no egress to
huggingface.co, and then it should be a separate image tag — a 5GB image for the
default `anthropic` path, which needs none of it, is pure cost.

Either way the first question after a cold start is slow. The models load in a
worker thread, so the rest of the service keeps serving while it happens.

Verified on the built image: the 116-package `orchestrator workflows` set
resolves and installs cleanly for cp314 on linux/amd64 — no missing wheel, no
source build, ~191s. `llm_provider=orchestrator` additionally **requires** a key
and will not start without one, same as `anthropic`.

## Run

```bash
cp .env.docker.example .env.docker      # then fill in ANTHROPIC_API_KEY
docker volume create legallens-data
docker run --rm -p 8000:8000 \
  --env-file .env.docker \
  -v legallens-data:/data \
  --name legallens-backend \
  legallens-backend:local
```

> You already have something listening on `127.0.0.1:8000`. If `curl` returns a
> health body mentioning the orchestrator and 57 passages, that is the local
> `uv run uvicorn`, not the container — publish on `-p 8001:8000` instead.

### Environment variables

Every one has a working default except the key. All are read by
`Settings` (`src/legal_lens/config.py`) under the `LEGAL_LENS_` prefix, except
the three uvicorn settings and `ANTHROPIC_API_KEY`.

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required** for `llm_provider=anthropic` — the service will not start without it (see below). `LEGAL_LENS_LLM_PROVIDER=echo` needs no key at all. |
| `ANTHROPIC_WORKSPACE_ID` | — | Only for a key not already scoped to a workspace. |
| `PORT` | `8000` | |
| `WORKERS` | `1` | **Leave at 1.** See below. |
| `GRACEFUL_TIMEOUT` | `30` | Seconds uvicorn waits on in-flight SSE streams at shutdown. |
| `FORWARDED_ALLOW_IPS` | `*` | Trust `X-Forwarded-For` from the proxy in front. |
| `LEGAL_LENS_DATA_DIR` | `/data` | SQLite db, uploaded originals, live Qdrant index. **Mount a volume.** |
| `LEGAL_LENS_ENVIRONMENT` | `prod` | `prod` disables `/api/docs` and `/api/openapi.json`. Use `dev` to get them. |
| `LEGAL_LENS_LOG_LEVEL` | `INFO` | |
| `LEGAL_LENS_LLM_PROVIDER` | `anthropic` | `anthropic` \| `echo` \| `orchestrator` |
| `LEGAL_LENS_PROBE_LLM_ON_STARTUP` | `true` | A one-token vendor call at boot so a revoked key reads as `degraded` rather than healthy. Adds up to 10s to startup — relevant to a tight startup probe. |

No secret is baked into the image. In AKS, everything above goes in a ConfigMap
except `ANTHROPIC_API_KEY`, which goes in a Secret.

## Check it works

```bash
curl -s http://localhost:8000/api/health | jq          # probe target, 200 ok|degraded
open http://localhost:8000/api/docs                    # needs LEGAL_LENS_ENVIRONMENT=dev
curl -s http://localhost:8000/api/matters | jq          # bootstrapped matter m-001
curl -s http://localhost:8000/api/metrics | jq

# a real end-to-end call: upload, ask, stream the answer
curl -s -X POST http://localhost:8000/api/documents -F file=@some-contract.pdf -F matterId=m-001
CID=$(curl -s -X POST http://localhost:8000/api/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"New conversation","matterId":"m-001"}' | jq -r .id)
AID=$(curl -s -X POST http://localhost:8000/api/chat/message \
  -H 'Content-Type: application/json' \
  -d "{\"conversationId\":\"$CID\",\"content\":\"What are the termination triggers?\"}" | jq -r .assistantMessageId)
curl -N "http://localhost:8000/api/chat/stream?messageId=$AID"
```

`/api/health` answers **200 whether the status is `ok` or `degraded`**, so a
liveness probe on it will not restart a pod over a key that is present but
revoked. A readiness probe wanting the stricter reading has to parse `.status`
from the body.

### A missing key fails startup, and the container does not exit

`llm_provider=anthropic` without `ANTHROPIC_API_KEY` is not a degraded service —
`AnthropicProvider.__init__` raises, so the lifespan never completes:

```
RuntimeError: llm_provider='anthropic' needs LEGAL_LENS_ANTHROPIC_API_KEY
ERROR:    Application startup failed. Exiting.
```

Despite "Exiting", **the container stays in `running` with exit code 0** and
every connection is reset. Verified: still `running` after ten minutes, with
aiosqlite's non-daemon `_connection_worker_thread` the only thread left besides
uvicorn's — the lifespan raised after `db.connect()` and before its `yield`, so
the `finally` that would have closed the connection never ran. Passing
`--workers 1` or omitting it makes no difference; both hang.

What this costs in AKS: no non-zero exit means no CrashLoopBackOff and no
clear reason in `kubectl describe`. Only the probe catches it, so **the liveness
probe is doing real work here, not just ceremony** — do not omit it, and keep the
startup probe's `failureThreshold` tight enough that a bad config surfaces in
seconds rather than minutes. The HEALTHCHECK in the image does mark the container
unhealthy.

The clean fix belongs in `main.py`, not here: close the database if the
pre-`yield` section of the lifespan raises (an `AsyncExitStack`, or a
`try/except` around the `Services` construction that awaits `db.close()` before
re-raising). I have not verified that it makes the process exit — a standalone
repro of the leaked thread exits on its own, because the event loop is closed by
then and the thread errors out, which is not the situation under uvicorn.

## With the frontend container

`../docker-compose.yml` wires both, waits for this service's healthcheck before
starting the frontend, and keeps the data volume across `down`:

```bash
cd ..                                   # the Legal Lens directory
cp backend/.env.docker.example backend/.env.docker    # then fill in the key
docker compose up --build
open http://localhost:8080              # app; :8001 reaches this service directly
```

By hand, if you would rather not use Compose — the frontend image proxies `/api`
to `BACKEND_URL`, so one network keeps the browser same-origin:

```bash
docker network create legallens
docker run -d --name legallens-backend --network legallens \
  --env-file .env.docker -v legallens-data:/data legallens-backend:local
docker run -d --name legallens-frontend --network legallens -p 8080:8080 \
  -e BACKEND_URL=http://legallens-backend:8000 legallens-frontend:local
open http://localhost:8080
```

## Logs, shell, cleanup

```bash
docker logs -f legallens-backend                  # JSON lines on stdout
docker logs legallens-backend 2>&1 | jq -c 'select(.msg=="request")'
docker exec -it legallens-backend sh              # shell as uid 10001
docker exec legallens-backend ls -la /data /data/objects
docker exec legallens-backend python -c "import legal_lens, sys; print(legal_lens.__version__, sys.version)"
docker stop legallens-backend                     # SIGTERM -> lifespan shutdown closes the db
docker volume rm legallens-data                   # destroys every uploaded document
```

## Notes for AKS

**One replica, one worker.** This is the constraint to design the manifest
around. The service keeps state in the process that a second worker or replica
would silently fracture:

- one SQLite connection serialised through an `asyncio.Lock` (`db/session.py`);
  that lock means nothing across processes
- the in-process rate limiter (`middleware.py`) — N replicas means N× the limit
- the `/api/metrics` sink, which is per-process
- the cancellation registry behind `POST /api/chat/stop`; a second replica never
  sees the stop
- under the orchestrator path, an embedded Qdrant holding an **exclusive lock**
  on its directory — a second pod cannot even start

So: `replicas: 1`, `strategy: Recreate` (a RollingUpdate would briefly run two
pods against one ReadWriteOnce PVC), and a PVC rather than an `emptyDir`.

**Stateful by design.** `/data` holds the database, every uploaded original and
the live vector index. Without a volume, a restart loses every document.

**Logs** are JSON on stdout, one object per line, with `request_id` on every
record. The app disables uvicorn's access log itself
(`observability.configure_logging`) because its own request middleware already
emits one.

**SIGTERM** reaches uvicorn as PID 1 and runs the lifespan shutdown — verified:
0.39s to a clean `service stopped`, with the database closed. Set
`terminationGracePeriodSeconds` above `GRACEFUL_TIMEOUT` (30), and above the
longest expected generation if you do not want rollouts cutting answers off.

**Read-only root filesystem works, but needs a writable `/tmp`.** Starlette
spools any upload over 1MB to a temporary file, so without it every upload above
that size fails with `400 "There was an error parsing the body"` — which reads
like a malformed request, not a filesystem problem. Verified both ways.

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
volumeMounts:
  - { name: data, mountPath: /data }     # PVC, ReadWriteOnce
  - { name: tmp,  mountPath: /tmp }      # emptyDir
```

With `docker run`, that is
`--read-only --tmpfs /tmp:uid=10001,gid=10001 -v legallens-data:/data`.

**Probes.** `/api/health` on all three; give the startup probe room for the key
probe (up to 10s) and, on the orchestrator path, for the model load.

**CORS** (`LEGAL_LENS_CORS_ORIGINS`) only matters if something calls this
service cross-origin. Behind the frontend's `/api` proxy, or an ingress routing
both under one host, the browser is same-origin and no preflight happens.
