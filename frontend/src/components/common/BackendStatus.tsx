import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CloudOff } from "lucide-react";
import { api } from "@/lib/api";
import { USE_MOCKS } from "@/lib/constants";

/** Which `checks` entries to name in the banner, in the order they read best. */
const LABELS: Record<string, string> = {
  db: "database",
  object_store: "document storage",
  llm_provider: "model provider",
  vector_store: "vector store",
};

/**
 * A one-line report on whether the backend can actually do what the UI offers.
 *
 * Only rendered against the real API: in mock mode there is no backend whose health
 * could be reported, and a banner describing a service that isn't involved would be
 * noise. The distinction that matters here is `degraded` versus unreachable —
 * degraded still serves every read path, so the banner informs rather than blocks.
 */
export function BackendStatus() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    enabled: !USE_MOCKS,
    // Slow enough to be invisible in normal use, fast enough that starting the
    // backend after the browser clears the banner without a reload.
    refetchInterval: 15_000,
    retry: false,
  });

  if (USE_MOCKS) return null;

  if (isError) {
    return (
      <Banner tone="risk" icon={<CloudOff size={14} className="shrink-0" />}>
        <span className="font-medium">Backend unreachable.</span> Start it with{" "}
        <code className="rounded bg-risk-bg px-1 font-mono text-micro">
          uv run uvicorn legal_lens.main:app --reload --port 8000
        </code>
      </Banner>
    );
  }

  if (!data || data.status === "ok") return null;

  const missing = Object.entries(data.checks)
    .filter(([, state]) => !state.startsWith("ok"))
    .map(([key]) => LABELS[key] ?? key);

  return (
    <Banner tone="attention" icon={<AlertTriangle size={14} className="shrink-0" />}>
      <span className="font-medium">Running degraded.</span> Unavailable:{" "}
      {missing.join(", ")}. Reads still work.
    </Banner>
  );
}

function Banner({
  tone,
  icon,
  children,
}: {
  tone: "risk" | "attention";
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  const palette =
    tone === "risk"
      ? "border-risk-br bg-risk-bg text-risk-fg"
      : "border-attention-br bg-attention-bg text-attention-fg";
  return (
    <div
      role="status"
      className={`flex items-center gap-2 border-b px-4 py-1.5 text-caption ${palette}`}
    >
      {icon}
      <p className="truncate">{children}</p>
    </div>
  );
}
