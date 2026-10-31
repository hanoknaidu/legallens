# Running the frontend in Docker

The image is a two-stage build: Node compiles the bundle, nginx serves it. Nothing
in it needs a secret — a static bundle is downloaded whole by the browser, so
anything baked in would be public anyway.

## Build

```bash
cd frontend
docker build -t legallens-frontend:local .
```

For AKS, which is x86, build the platform explicitly — Docker defaults to the
host's architecture, and an arm64 image will not start on an amd64 node:

```bash
docker build --platform linux/amd64 -t legallens-frontend:local-amd64 .
```

### Build arguments

| Arg | Default | Why it is a build arg and not an env var |
|---|---|---|
| `VITE_USE_MOCKS` | `false` | Vite inlines `import.meta.env.VITE_*` into the JS at build time, so this cannot be changed after the image exists. `true` builds a bundle served entirely by the MSW mock layer — the only path with demo content. |

## Run

```bash
docker run --rm -p 8080:8080 --env-file .env.docker --name legallens-frontend legallens-frontend:local
```

Copy `.env.docker.example` to `.env.docker` first. Both variables have defaults
baked in, so the image also runs with no `--env-file` at all:

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND_URL` | `http://backend:8000` | Where `/api/*` is forwarded. No trailing slash. |
| `PORT` | `8080` | Port nginx listens on. Must stay above 1024 — nothing runs as root. |

### Pointing at a backend

The app calls the relative path `/api` (`src/lib/constants.ts`), and the backend
mounts its routers under the same `/api` prefix, so nginx forwards the path
unchanged. The browser therefore stays same-origin and CORS never applies —
the same arrangement the Vite proxy makes in dev.

Or let `../docker-compose.yml` do it: it builds both images, waits for the
backend's healthcheck before starting this one, and sets `BACKEND_URL` to the
service name.

```bash
cd .. && docker compose up --build && open http://localhost:8080
```

By hand:

```bash
# backend running on the Mac host (uv run uvicorn ... --port 8000)
docker run --rm -p 8080:8080 -e BACKEND_URL=http://host.docker.internal:8000 legallens-frontend:local

# backend as a container on a shared network
docker network create legallens
docker run --rm -p 8080:8080 --network legallens -e BACKEND_URL=http://legallens-backend:8000 legallens-frontend:local
```

The frontend starts whether or not the backend resolves: `/api` calls return 502
until it is there, and the UI's own `BackendStatus` reports it.

## Check it works

```bash
curl -i http://localhost:8080/healthz             # 200 ok   <- probe target
curl -o /dev/null -w '%{http_code}\n' http://localhost:8080/chat/new   # 200, SPA fallback
open http://localhost:8080                        # the app
curl -s http://localhost:8080/api/health | jq     # through the proxy to the backend
open http://localhost:8080/api/docs               # backend OpenAPI, proxied (non-prod only)
```

`/healthz` is answered by nginx with no call to the backend, which is what makes it
a usable AKS liveness *and* readiness target: a readiness probe that reached
through to the API would pull the UI out of rotation whenever the API was briefly
unwell.

## Logs, shell, cleanup

```bash
docker logs -f legallens-frontend                 # access + error log, both on stdout/stderr
docker exec -it legallens-frontend sh             # shell in the container (as uid 101)
docker exec legallens-frontend cat /etc/nginx/conf.d/default.conf   # the rendered config
docker stop legallens-frontend                    # SIGQUIT -> graceful drain
docker rmi legallens-frontend:local
```

## Notes for AKS

- Stateless: the only writes are nginx's temp and pid files under `/tmp`.
- Logs go to stdout and stderr; nothing is written to a log file.
- `STOPSIGNAL SIGQUIT` (from the base image) drains in-flight requests. SSE chat
  streams are long-lived, so set `terminationGracePeriodSeconds` to more than the
  longest expected generation, or a rollout will cut answers off.
- Runs as uid 101, so `runAsNonRoot: true` needs no exception.
- Read-only root filesystem works, with:

  ```yaml
  securityContext:
    readOnlyRootFilesystem: true
  volumeMounts:
    - { name: tmp, mountPath: /tmp }
    - { name: nginx-conf, mountPath: /etc/nginx/conf.d }
  ```

  Both must be writable by uid 101 — the entrypoint renders the config template
  into `/etc/nginx/conf.d` at start. If it cannot, it logs an error and nginx
  comes up with no server block, which looks like a connection reset rather than
  a crash. With `docker run`, that is
  `--tmpfs /tmp:uid=101,gid=101 --tmpfs /etc/nginx/conf.d:uid=101,gid=101`.
- No IPv6 listener: binding `[::]` fails on a host without IPv6, and kubelet
  probes over IPv4. Add `listen [::]:${PORT};` to the template if the cluster is
  dual-stack.
