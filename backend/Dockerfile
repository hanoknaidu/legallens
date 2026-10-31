# syntax=docker/dockerfile:1

# =============================================================================
# Stage 1 — resolve and install dependencies
#
# python:3.14.10-slim-trixie rather than alpine: every wheel this project needs
# is built for manylinux, and musl forces a source build of anything without an
# alpine wheel — torch, under the `workflows` extra, ships none at all. Slim is
# also why no compiler is installed below: the locked dependency set is wheels
# all the way down.
#
# 3.14 matches the interpreter the project is developed against (.venv is
# 3.14.7) and that uv.lock resolves for. The floor in pyproject.toml is 3.12, so
# this can move down, but then it should move down deliberately.
# =============================================================================
FROM python:3.14.10-slim-trixie AS build

# uv as a binary from its own published image rather than `pip install uv`:
# nothing is installed into the Python environment to do the installing, and the
# version is pinned to the one the lockfile was written with.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# UV_COMPILE_BYTECODE: write .pyc at build time, so the first request after a
#   cold start is not also compiling the standard library's worth of imports.
# UV_LINK_MODE=copy: the cache below lives on a different filesystem, and uv's
#   default hardlink strategy warns and falls back on every package otherwise.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Which optional dependency sets to install, as a space-separated list — see
# pyproject.toml's [project.optional-dependencies]. Empty by default, which is
# the configuration that boots with no local models and no vector server:
# llm_provider=anthropic, embedding_provider=hashing, vector_store=memory.
#
#   EXTRAS="ocr"                 scanned-PDF OCR (FR-2 rejects scans without it)
#   EXTRAS="orchestrator"        the three-workflow pipeline, no retrieval stack
#   EXTRAS="orchestrator workflows"  adds torch, Qdrant, fastembed
#
# Read DOCKER.md before using `workflows`: on linux that resolution pulls the
# CUDA runtime and triton — several GB a CPU node will never execute — and the
# pipeline then downloads ~4.5GB of models on its first question.
ARG EXTRAS=""

# The lockfile and the manifest alone, so the dependency layer survives any edit
# under src/. --no-install-project is what makes that split possible: it
# installs the 183 locked third-party packages and stops short of the project
# itself, which is the only part that changes when code changes.
#
# --frozen: install exactly uv.lock and fail if pyproject.toml has drifted from
# it. A build is the wrong place to resolve a new version of anything.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project \
        $(for e in ${EXTRAS}; do printf -- '--extra %s ' "$e"; done)

# Then the project. schema.sql and the five orchestrator prompt .md files are
# package data loaded by path at runtime (db/session.py, orchestrator/), so they
# have to be in the wheel — hatchling includes them because they sit inside the
# package directory.
COPY src ./src

# --no-editable installs a built wheel rather than a link back to /app/src, so
# the runtime stage needs nothing but the virtualenv it copies.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable \
        $(for e in ${EXTRAS}; do printf -- '--extra %s ' "$e"; done)


# =============================================================================
# Stage 2 — run it
#
# The same slim base, with the virtualenv copied in and no uv, no lockfile and
# no build cache. Nothing here can resolve or install a package.
# =============================================================================
FROM python:3.14.10-slim-trixie AS runtime

# A fixed high uid, not a name: AKS `runAsUser` wants a number, and a numeric
# USER is also what lets a pod security context assert non-root without
# resolving /etc/passwd.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# PYTHONUNBUFFERED: the app logs JSON to stdout (observability.configure_logging)
#   and a buffered stream loses the last lines when a pod is killed.
# PYTHONDONTWRITEBYTECODE: the .pyc are already in the venv from the build
#   stage; writing more at runtime only dirties a read-only filesystem.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---- runtime settings, all overridable -------------------------------------
# Not secrets, and no secret belongs in an image: the API key arrives as an
# environment variable or a mounted AKS secret at run time. See .env.docker.example.
#
# WORKERS defaults to 1 and should stay there. This service keeps per-process
# state that a second worker would silently fracture: one SQLite connection
# serialised through an asyncio.Lock (db/session.py), the in-process rate
# limiter (middleware.py), the /metrics sink, the cancellation registry behind
# POST /api/chat/stop, and — under the orchestrator path — an embedded Qdrant
# that takes an exclusive lock on its directory. Scale by making the request
# path less synchronous, not by adding workers.
ENV PORT=8000 \
    WORKERS=1 \
    GRACEFUL_TIMEOUT=30 \
    FORWARDED_ALLOW_IPS="*" \
    LEGAL_LENS_DATA_DIR=/data \
    LEGAL_LENS_ENVIRONMENT=prod \
    LEGAL_LENS_LOG_LEVEL=INFO

COPY --from=build --chown=app:app /app/.venv /app/.venv

# The SQLite database, the uploaded originals and the live Qdrant index all land
# here (config.py: db_path, objects_path, orchestrator_qdrant_path). Created and
# owned at build time so the directory works whether or not a volume is mounted
# over it. Deliberately no VOLUME instruction: it would make the path opaque to
# `docker build`, and in AKS the mount is the PVC's business, not the image's.
RUN install -d -o app -g app /data

WORKDIR /app
USER 10001

EXPOSE 8000

# GET /api/health is the probe target. It answers 200 whether the service is
# "ok" or "degraded", which is the right behaviour for liveness — a missing
# vendor key degrades the service without stopping it serving — and means a
# readiness probe wanting the stricter reading has to look at the JSON body.
# urllib rather than curl, because slim ships no curl and the interpreter is
# already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/api/health',timeout=4).read()"]

# `exec` so uvicorn replaces the shell and becomes PID 1, which is what makes it
# receive SIGTERM directly and shut down cleanly instead of being killed after
# the grace period. sh -c is needed only to expand the settings above.
#
# --proxy-headers with --forwarded-allow-ips: nginx or an AKS ingress terminates
# the connection, so without these every client address logs as the proxy's.
# --timeout-graceful-shutdown bounds the wait on in-flight SSE chat streams,
# which would otherwise hold a terminating pod open for the length of a
# generation.
CMD ["sh", "-c", "exec uvicorn legal_lens.main:app \
    --host 0.0.0.0 \
    --port ${PORT} \
    --workers ${WORKERS} \
    --proxy-headers \
    --forwarded-allow-ips ${FORWARDED_ALLOW_IPS} \
    --timeout-graceful-shutdown ${GRACEFUL_TIMEOUT}"]
