# syntax=docker/dockerfile:1

# =============================================================================
# Stage 1 — build the static bundle
#
# Node exists only to produce dist/. Keeping it in its own stage means the
# toolchain — node, the npm cache, ~300MB of node_modules — never reaches the
# image that ships.
# =============================================================================
FROM node:22.23.4-alpine AS build

WORKDIR /app

# `import.meta.env.VITE_*` is inlined into the JS by the bundler, so this is a
# build-time input and not a runtime one: a built container cannot be switched
# over to the mock layer afterwards. False is what production wants, and is
# also what `vite build` would default to on its own, since .env.development is
# only read in dev mode (see src/lib/constants.ts).
ARG VITE_USE_MOCKS=false
ENV VITE_USE_MOCKS=${VITE_USE_MOCKS}

# Manifest and lockfile before the sources, so editing a component reuses the
# install layer instead of reinstalling 200-odd packages.
# `npm ci` (not `npm install`) installs exactly the lockfile and fails if
# package.json has drifted from it — a build should not resolve a new version.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# Then the sources, listed rather than `COPY . .`, so a stray local dist/,
# .env.development.local or tsbuildinfo cannot change what gets built. The
# .dockerignore is the second half of that guarantee.
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

# `npm run build` is `tsc -b && vite build`, so the type check runs first and a
# type error fails the image instead of shipping.
RUN npm run build


# =============================================================================
# Stage 2 — serve it
#
# A static bundle needs a web server, not Node: `vite preview` is a dev
# convenience and says so in its own docs. nginx-unprivileged rather than plain
# nginx because it is the same image already set up to run as uid 101, with its
# pid file and temp paths under /tmp — so there is no chown dance here, and a
# restrictive AKS pod security context needs no exception. A read-only root
# filesystem also works, but only with /tmp and /etc/nginx/conf.d mounted as
# tmpfs owned by uid 101; see DOCKER.md.
# =============================================================================
FROM nginxinc/nginx-unprivileged:1.31.7-alpine AS runtime

# Everything environment-specific is here, and nothing else is. The template is
# rendered to /etc/nginx/conf.d/default.conf by the base image's entrypoint at
# container start, so one image serves local, staging and prod unchanged.
# The filter restricts substitution to these two names, so nginx's own $uri,
# $host and $request_uri survive the pass untouched.
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS makes the base image read /etc/resolv.conf
# into ${NGINX_LOCAL_RESOLVERS} before the template is rendered; the config
# needs it for the deferred DNS described there.
ENV PORT=8080 \
    BACKEND_URL=http://backend:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER='^(PORT|BACKEND_URL|NGINX_LOCAL_RESOLVERS)$'

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

USER nginx

# Default, not a contract: the server listens on ${PORT}. Above 1024 because
# nothing here runs as root.
EXPOSE 8080

# Answered by nginx alone, with no call to the backend, so it reports whether
# *this* container can serve the app. AKS liveness and readiness probes can
# point at the same path — a readiness check that reached through to the API
# would take the whole UI out whenever the API was briefly unwell.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT}/healthz" || exit 1

# CMD and STOPSIGNAL come from the base image: `nginx -g daemon off` in the
# foreground as PID 1, with SIGQUIT for a graceful drain.
