import { MessageSquareText, FileSearch, PenLine } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { INTENT_LABEL } from "@/lib/constants";
import type { Intent } from "@/types/domain";

const ICON = { ask: MessageSquareText, summary: FileSearch, draft: PenLine };

export function IntentBadge({ intent }: { intent: Intent }) {
  const Icon = ICON[intent];
  return (
    <Badge tone="brand">
      <Icon size={11} />
      {INTENT_LABEL[intent]}
    </Badge>
  );
}
