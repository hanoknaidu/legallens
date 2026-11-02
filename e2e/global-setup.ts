import path from "node:path";
import { request } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const UI = process.env.E2E_BASE_URL ?? "http://localhost:5173";

/** Where the signed-in cookie jar is written. `use.storageState` reads it. */
export const STORAGE_STATE = path.resolve(__dirname, ".auth/state.json");

const EMAIL = process.env.E2E_EMAIL ?? "attorney@firm.test";
const PASSWORD = process.env.E2E_PASSWORD ?? "";

/**
 * Refuses to run against a half-started stack.
 *
 * Every failure mode below produces a wall of unrelated assertion failures if the run
 * starts anyway, so each one is named here instead.
 */
export default async function globalSetup() {
  const ctx = await request.newContext();

  let health: Record<string, unknown>;
  try {
    const res = await ctx.get(`${API}/api/health`, { timeout: 10_000 });
    health = await res.json();
  } catch {
    throw new Error(
      `The backend is not answering on ${API}.\n\n` +
        `  cd backend && LEGAL_LENS_LLM_PROVIDER=orchestrator \\\n` +
        `    LEGAL_LENS_INTENT_ROUTER=model uv run uvicorn legal_lens.main:app --port 8000\n`,
    );
  }

  try {
    const res = await ctx.get(UI, { timeout: 10_000 });
    if (!res.ok()) throw new Error(String(res.status()));
  } catch {
    throw new Error(`The frontend is not answering on ${UI}.\n\n  cd frontend && npm run dev\n`);
  }

  // The mock layer would make every assertion below pass against fixtures, which is
  // the one outcome these tests exist to rule out.
  const mocks = await ctx.get(`${UI}/.env.development`).catch(() => null);
  if (mocks?.ok()) {
    const body = await mocks.text();
    if (/VITE_USE_MOCKS\s*=\s*true/.test(body)) {
      throw new Error("VITE_USE_MOCKS=true — these tests must run against the real backend.");
    }
  }

  const checks = (health.checks ?? {}) as Record<string, string>;
  const llm = checks.llm_provider ?? "";
  if (!llm.startsWith("ok")) {
    throw new Error(
      `The model provider is not usable: ${llm}\n` +
        `Generation tests need a working key — run \`python3 check_key.py\` in backend/.`,
    );
  }

  // ---------------------------------------------------------------------
  // Sign in once, and hand every spec the cookie.
  //
  // The app has required a session since Phase 2 (docs/auth.md), so without this
  // every spec would open the login page and fail on a missing composer — 73
  // identical, unhelpful failures. Signing in through the real API rather than
  // through the form keeps that one flow under test in `10-auth.spec.ts` instead of
  // repeated 73 times as setup.
  // ---------------------------------------------------------------------
  // These specs drive the authenticated app: they assert 401s, sign-in failures and
  // per-user isolation, none of which exist in a showcase build. Running them against
  // one produces a wall of failures that say nothing about the app, so it stops here
  // instead — the same policy as the checks above.
  const session = await ctx.get(`${API}/api/auth/session`, { timeout: 10_000 });
  const authRequired = Boolean((await session.json()).authRequired);
  if (!authRequired) {
    throw new Error(
      "The backend is a showcase build (LEGAL_LENS_REQUIRE_AUTH=false), where no " +
        "route requires a session.\n\n" +
        "These tests drive the authenticated app. Start the backend with:\n\n" +
        "  cd backend && LEGAL_LENS_REQUIRE_AUTH=true \\\n" +
        "    LEGAL_LENS_LLM_PROVIDER=orchestrator uv run uvicorn legal_lens.main:app --port 8000\n",
    );
  }

  if (!PASSWORD) {
    throw new Error(
      "E2E_PASSWORD is not set, and the app needs a session.\n\n" +
        "  export E2E_EMAIL=attorney@firm.test\n" +
        "  export E2E_PASSWORD=...\n\n" +
        "Create the account first if it does not exist:\n" +
        "  cd backend && uv run python -m legal_lens.auth.cli create attorney@firm.test\n",
    );
  }

  const signIn = await ctx.post(`${API}/api/auth/signin`, {
    data: { email: EMAIL, password: PASSWORD },
    timeout: 15_000,
  });
  if (!signIn.ok()) {
    const body = await signIn.text().catch(() => "");
    throw new Error(
      `Sign-in failed for ${EMAIL} (${signIn.status()}). ${body}\n\n` +
        "Check the password, or reset it:\n" +
        `  cd backend && uv run python -m legal_lens.auth.cli passwd ${EMAIL}\n`,
    );
  }

  // The cookie is scoped to /api and HttpOnly, so it cannot be set from page script —
  // saving the context's own state is what carries it into the browser.
  await ctx.storageState({ path: STORAGE_STATE });

  // eslint-disable-next-line no-console
  console.log(
    `\n  backend ${API} · status=${health.status} · llm=${llm}` +
      `\n  orchestrator=${checks.orchestrator ?? "n/a"}` +
      `\n  signed in as ${EMAIL}\n`,
  );

  await ctx.dispose();
}
