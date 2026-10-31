import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import type { IngestStatus as Status } from "@/types/domain";

/**
 * The four ingestion states, in one place.
 *
 * The library, the document card, the viewer panel and the scope picker all have to say
 * the same thing about the same document. Four copies of this map would eventually say
 * four different things, and "Ready" in one place with "Indexing" in another is a bug the
 * user reads as the system not knowing what it is doing.
 */
export const STATUS_META: Record<
  Status,
  { label: string; tone: "neutral" | "grounded" | "processing" | "risk"; Icon: typeof Clock; spin?: boolean }
> = {
  queued: { label: "Queued", tone: "neutral", Icon: Clock },
  processing: { label: "Indexing", tone: "processing", Icon: Loader2, spin: true },
  ready: { label: "Ready", tone: "grounded", Icon: CheckCircle2 },
  // Not "Failed": the document is intact, it is the indexing that did not happen, and
  // the distinction is what tells the user whether to re-upload or to convert.
  failed: { label: "Not indexed", tone: "risk", Icon: AlertTriangle },
};

export function IngestStatus({ status, size = "sm" }: { status: Status; size?: "sm" | "md" }) {
  const { label, tone, Icon, spin } = STATUS_META[status];
  return (
    <Badge tone={tone} size={size}>
      <Icon size={11} className={spin ? "animate-spin" : undefined} aria-hidden="true" />
      {label}
    </Badge>
  );
}
