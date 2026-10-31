import type { Intent } from "@/types/domain";

export const API_BASE = "/api";

/**
 * Whether to serve the app from the MSW mock layer instead of the real backend.
 *
 * **Defaults to off**: the real backend is the product, and a default that quietly
 * served fixtures made "it works on my machine" ambiguous. Set
 * `VITE_USE_MOCKS=true` to go back to the mock layer — useful with no backend
 * running, and the only path that ships demo content, since a real database starts
 * empty.
 *
 * Nothing above `lib/api.ts` knows which mode is active.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";

export const INTENT_LABEL: Record<Intent, string> = {
  ask: "Ask",
  summary: "Summary",
  draft: "Draft",
};

/** The nine summary fields fixed by the BRD. Order is part of the contract. */
export const SUMMARY_FIELDS = [
  { key: "parties", label: "Parties" },
  { key: "term", label: "Term & renewal" },
  { key: "obligations", label: "Obligations" },
  { key: "payment", label: "Payment terms" },
  { key: "termination", label: "Termination triggers" },
  { key: "governingLaw", label: "Governing law & venue" },
  { key: "liability", label: "Liability & indemnification" },
  { key: "unusual", label: "Unusual provisions" },
  { key: "openItems", label: "Open items" },
] as const;

/**
 * The placeholder name for a conversation nobody has named yet.
 *
 * `POST /conversations` requires a title, so this is what the client sends for a new
 * one. The string is load-bearing: the backend renames a conversation from its first
 * user turn only while the stored title still equals this placeholder, so a drift
 * between the two sides silently stops auto-naming. A backend test pins them together.
 */
export const DEFAULT_CONVERSATION_TITLE = "New conversation";

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_UPLOAD_PAGES = 500;
