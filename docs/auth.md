# Authentication

Phase 2 of the BRD roadmap (§10), filling the seam §6.1 left open. Phase 1 shipped
with no login as a documented scope cut, on the bet that carrying `owner_id` on every
table from day one would make authentication "a middleware and frontend change rather
than a schema migration."

That bet paid. No existing table changed. `deps.current_owner_id` used to return a
constant and now resolves a session cookie; every router that already took `OwnerDep`
became authenticated without being edited, because every query was already filtering
on the value it returns.

## There is no sign-in in the app

**The frontend has no login page.** It was built, demonstrated and then removed at
the owner's request; the app opens straight into the chat session. Nothing in the UI
signs in, signs out, or shows who you are.

**`LEGAL_LENS_REQUIRE_AUTH` is off** to match. No API route requires a session, and
every request is attributed to the single Phase 1 owner — exactly how the service
behaved before authentication existed. There is no access control at all, and the BRD
classes what is stored here as privileged attorney-client material (§8.2), so this is
a build for a laptop and a demo rather than for a deployment.

It announces itself, because a build with no access control left running somewhere
real is the mistake worth catching:

- a `WARNING` at every boot — `SHOWCASE BUILD: authentication is NOT enforced`
- `/api/health` → `"auth": "ok (SHOWCASE BUILD: no sign-in required, …)"`

### What survives, and what turning the flag on would and would not give you

The backend implementation is intact and still tested: `users` and `sessions` tables,
scrypt hashing, server-side sessions, sign-in throttling, the account CLI, and
per-user isolation enforced by `deps.current_owner_id`. `LEGAL_LENS_REQUIRE_AUTH=true`
brings all of it back at the API, and the test suite covers it.

**What it would not give you is a usable browser app.** With enforcement on and no
login page, the SPA's first request gets a 401 and there is no screen to resolve it —
the API would be usable only by a client that drives `POST /auth/signin` itself.
Turning authentication on for the browser means restoring a login page first. The one
that was removed lived at `frontend/src/pages/LoginPage.tsx` with a route guard at
`components/auth/RequireAuth.tsx`; both are in the history of this branch rather than
gone.

The e2e suite drives the app as it is now — no sign-in step, 73 specs in 9 files.

## What it is, with authentication on

Email and password, verified against a server-side session.

| Decision | Choice | Why not the alternative |
|---|---|---|
| Credential | Email + password | No SSO/OIDC provider is configured for this project yet. See [Swapping in SSO](#swapping-in-sso) — the seam for it is the same one this used. |
| Password hash | scrypt (`hashlib`) | argon2id is the better primitive, but it is a C extension and a new dependency. scrypt is memory-hard, OpenSSL-backed and already in the image, so the shipped artifact stays at the 221MB `backend/DOCKER.md` documents and `uv.lock` is untouched. |
| Session | Opaque token, row in `sessions` | A JWT stays valid until it expires no matter what the server thinks. Sign-out has to actually end a session for privileged material (§8.2), and the only fix for a JWT is a revocation list — which is this table, minus the benefit. |
| Transport | `HttpOnly` cookie | A token in `localStorage` is readable by the page's own JavaScript by definition, so any XSS bug anywhere in the app exfiltrates the session. The cost is CSRF exposure, handled below. |
| Account creation | **Open by default** — see [Open sign-in](#open-sign-in) | Configurable. With `LEGAL_LENS_OPEN_SIGNIN=false` accounts come only from the operator CLI or first-run bootstrap. |

## Open sign-in

**`LEGAL_LENS_OPEN_SIGNIN` is on by default.** An email with no account signs in and
gets one, created with the password submitted. The second visit is an ordinary
sign-in with that password.

Read the consequence plainly: **anyone who can reach the service can create an
account and start uploading and reading documents in their own library.** The BRD
classes what is stored here as privileged attorney-client material (§8.2), so for any
deployment reachable beyond a laptop this should be `false` and accounts should come
from the CLI.

The service says so at every boot, at `WARNING`:

```
open sign-in is ON: any email that signs in gets an account
```

and `/api/health` reports it, so the answer does not require reading the
deployment's environment:

```json
"auth": "ok (open sign-in: any email gets an account)"
```

### What it does not open

| Still refused | Why it matters |
|---|---|
| An existing account with the wrong password | Provisioning happens only when there is no account, so this cannot be used to walk into someone else's library by typing their address. |
| A disabled account | Otherwise `auth.cli disable` would be undone by signing in again. |
| A password under 12 characters | The account is not created; the request is a 422. |

Accounts made this way are ordinary accounts — their own matter, their own library,
the same `owner_id` filtering as any other. Open sign-in changes *who may get in*,
not *what they can see*.

### What it costs

Enumeration resistance. With it off, an unknown address and a wrong password give
byte-identical replies, so the endpoint cannot be asked "is this person a client of
this firm?". With it on, a new address that submits a short password is told the
password is too short, while a known address is told the credentials are wrong — and
those two replies are distinguishable.

That is inherent to self-service sign-up, not an implementation detail that could be
tidied away: a form that provisions accounts necessarily behaves differently for an
address that has one.

Turn it off with:

```bash
LEGAL_LENS_OPEN_SIGNIN=false
```

## Creating accounts

```bash
cd backend
uv run python -m legal_lens.auth.cli create you@firm.com --name "Your Name"
uv run python -m legal_lens.auth.cli list
uv run python -m legal_lens.auth.cli passwd  you@firm.com   # also ends their sessions
uv run python -m legal_lens.auth.cli disable you@firm.com   # keeps their documents
```

The password is read with `getpass` and is never an argument — an argument ends up in
shell history, in `ps` output, and in whatever ships the logs.

A container has no shell to run that in, so a fresh deployment can create the first
account from the environment instead:

```bash
LEGAL_LENS_BOOTSTRAP_EMAIL=you@firm.com
LEGAL_LENS_BOOTSTRAP_PASSWORD=...            # at least 12 characters
```

Guarded twice: both must be set, **and** the users table must be empty. Leaving them
in the environment cannot reset an existing account — verified by restarting a
populated install with different values and watching it log
`bootstrap skipped: an account already exists`.

### The first account inherits Phase 1's data

The first user created adopts `owner-local`, the id Phase 1 wrote on every row. Any
document, conversation or audit entry from before there were accounts therefore belongs
to them. Every account after that gets a generated id and starts empty.

This matters if you intend the first account to be someone *other* than whoever used
the Phase 1 install — in that case create the real owner's account first, then the
others.

## What is enforced, and where

| Behaviour | Where |
|---|---|
| Every `/api` route except `/health`, `/metrics` and `/auth/*` needs a session | `deps.current_owner_id` via `OwnerDep` |
| One failure message for wrong password, unknown address and disabled account | `auth/service.py` |
| A missing account costs the same time as a real one | `auth/passwords.dummy_hash` |
| 5 failures per `email|ip` in 15 minutes, then a 15-minute lockout | `auth/throttle.py` |
| Sign-in mints a new token, never reuses the caller's | `auth/service.sign_in` |
| Sign-out deletes the row; a copied cookie is then worthless | `api/auth.sign_out` |
| A password change ends every session for that user | `auth/service.set_password` |
| Session expiry slides on use — 8h idle timeout, not a hard cap | `session_ttl_seconds` |
| Rate limiting is per session, falling back to source address | `middleware._caller_key` |

### CSRF

`SameSite=Lax` plus a same-origin frontend. The browser does not attach a Lax cookie
to a cross-site POST, and in every deployment the frontend reaches `/api` through a
proxy on its own origin — nginx in Docker, the Vite proxy in dev — so there is no
legitimate cross-origin authenticated request to allow. `allow_credentials=True` on
CORS is what makes `cors_origins` a list that may never become `["*"]`: a wildcard
origin with credentials is rejected by browsers, and if it were not it would hand any
site on the internet the user's cookie.

A double-submit token would add defence in depth. It is not here, and the reason is
that Lax already blocks the vector it would cover for a same-origin app — worth
revisiting if the API ever serves a client on another origin.

## Known limits

Stated rather than papered over.

1. **Throttle state is in-process.** Like the existing rate limiter, and sound for the
   same reason: the service is single-worker by design (`WORKERS` must stay 1 —
   `backend/DOCKER.md`). A second worker gives each its own counters. Moving both to
   Redis is the same change that makes multi-worker possible at all.
2. **Throttling keys on `email|ip`.** So one attacker cannot lock a real user out of
   their own account by guessing it from elsewhere. The trade is that a distributed
   guesser gets a fresh allowance per source address; a shared store with a
   per-account counter is the fix, and it arrives with (1).
3. **Rate limiting is per session, not per user.** Middleware runs before
   dependencies, so resolving the cookie to a user id there would cost a query on
   every request. A user with two browsers gets two allowances.
4. **No password reset flow.** No email sender is configured. An operator runs
   `auth.cli passwd`. A self-service reset needs mail, single-use tokens and their own
   expiry — a feature, not an omission to fix in passing.
5. **No MFA, no password history, no rotation policy.** All reasonable asks for a
   firm handling privileged material, and all out of scope here.
6. **There is no login page**, so `REQUIRE_AUTH=true` is API-only — see above.
7. **Open sign-in is on by default** when authentication is on, which trades away enumeration
   resistance and means the service is self-service unless configured otherwise.
8. **Sign-in events go to the log, not to `audit_log`.** That table's shape is
   per-generation (FR-20: feature, tokens, cost, grounding). Bending it to hold
   authentication events would make both harder to read.

## Swapping in SSO

The same seam. `deps.current_user` resolves a cookie to a `User`; an OIDC
implementation resolves a bearer token or an authorization-code exchange to the same
type, and nothing downstream changes — `OwnerDep` still yields `user.id`, and every
query still filters on it.

What would need deciding at that point: whether `users` rows are still created locally
and matched on email, or whether the provider's subject becomes `owner_id` directly.
Matching on email keeps existing data attached to existing people; using the subject is
cleaner and orphans everything already uploaded.

## Tests

`backend/tests/test_auth.py`, 49 tests, covering all three postures — enforcement
off, open sign-in, and operator-created accounts — including both sign-in modes — the open-mode
boundaries above, and a `strict_client` fixture that pins the byte-identical replies
`open_signin=false` restores. The one that justifies the rest:
`test_one_user_cannot_see_another_users_documents` drives two real sessions through
HTTP and asserts a document uploaded by one attorney is absent from the other's
library *and* unreachable by id — which is the failure that matters if an id ever
leaks, and the thing §6.1's owner column was for.

Retrieval is covered separately and was already owner-scoped before this change:
`Passage.owner_id` is in the Qdrant payload and `_scope_filter` makes it a `must`
condition, pinned by a two-tenant index test (`workflows-plan.md`, "Owner scoping is
now structural"). Without that, authentication alone would still have let one user's
question retrieve another's document text.
