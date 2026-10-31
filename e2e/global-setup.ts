import { request } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const UI = process.env.E2E_BASE_URL ?? "http://localhost:5173";

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

  // eslint-disable-next-line no-console
  console.log(
    `\n  backend ${API} · status=${health.status} · llm=${llm}` +
      `\n  orchestrator=${checks.orchestrator ?? "n/a"}\n`,
  );

  await ctx.dispose();
}
