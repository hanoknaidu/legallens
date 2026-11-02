# Authentication

Phase 2 of the BRD roadmap (§10), filling the seam §6.1 left open. Phase 1 shipped
with no login as a documented scope cut, on the bet that carrying `owner_id` on every
table from day one would make authentication "a middleware and frontend change rather
than a schema migration."

That bet paid. No existing table changed. `deps.current_owner_id` used to return a
constant and now resolves a session cookie; every router that already took `OwnerDep`
became authenticated without being edited, because every query was already filtering
on the value it returns.

## What it is

Email and password, verified against a server-side session.

| Decision | Choice | Why not the alternative |
|---|---|---|
| Credential | Email + password | No SSO/OIDC provider is configured for this project yet. See [Swapping in SSO](#swapping-in-sso) — the seam for it is the same one this used. |
| Password hash | scrypt (`hashlib`) | argon2id is the better primitive, but it is a C extension and a new dependency. scrypt is memory-hard, OpenSSL-backed and already in the image, so the shipped artifact stays at the 221MB `backend/DOCKER.md` documents and `uv.lock` is untouched. |
| Session | Opaque token, row in `sessions` | A JWT stays valid until it expires no matter what the server thinks. Sign-out has to actually end a session for privileged material (§8.2), and the only fix for a JWT is a revocation list — which is this table, minus the benefit. |
| Transport | `HttpOnly` cookie | A token in `localStorage` is readable by the page's own JavaScript by definition, so any XSS bug anywhere in the app exfiltrates the session. The cost is CSRF exposure, handled below. |
| Account creation | Operator CLI or first-run bootstrap | A firm's document library is not something a stranger joins. There is no sign-up route, and a test asserts there isn't one. |

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
6. **Sign-in events go to the log, not to `audit_log`.** That table's shape is
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

`backend/tests/test_auth.py`, 35 tests. The one that justifies the rest:
`test_one_user_cannot_see_another_users_documents` drives two real sessions through
HTTP and asserts a document uploaded by one attorney is absent from the other's
library *and* unreachable by id — which is the failure that matters if an id ever
leaks, and the thing §6.1's owner column was for.

Retrieval is covered separately and was already owner-scoped before this change:
`Passage.owner_id` is in the Qdrant payload and `_scope_filter` makes it a `must`
condition, pinned by a two-tenant index test (`workflows-plan.md`, "Owner scoping is
now structural"). Without that, authentication alone would still have let one user's
question retrieve another's document text.
