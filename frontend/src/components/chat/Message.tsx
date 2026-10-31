import { ShieldCheck, FileText } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Markdown } from "./Markdown";
import { IntentBadge } from "./IntentBadge";
import { AbstentionNotice } from "./AbstentionNotice";
import { IncompleteNotice } from "./IncompleteNotice";
import { MessageActions } from "./MessageActions";
import { SummaryCard } from "@/components/summary/SummaryCard";
import { DraftDocument } from "@/components/draft/DraftDocument";
import { useDocumentLookup } from "@/hooks/useDocuments";
import { useDocPanelStore } from "@/stores/useDocPanelStore";
import type { Message as Msg } from "@/types/domain";

export function UserMessage({ message }: { message: Msg }) {
  const lookup = useDocumentLookup();
  const openDocument = useDocPanelStore((s) => s.openDocument);
  const scoped = (message.scopedDocumentIds ?? []).map(lookup).filter((d) => !!d);

  return (
    <div className="flex flex-col items-end gap-1.5 py-5">
      <div className="max-w-[85%] rounded-lg rounded-br-xs bg-brand-600 px-3.5 py-2.5
                      text-body leading-relaxed text-white">
        {message.content}
      </div>
      {scoped.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1">
          {scoped.map((d) => (
            <button
              key={d.id}
              onClick={(e) => openDocument(d.id, 1, e.currentTarget)}
              disabled={d.status !== "ready"}
              aria-label={`Open ${d.title}`}
              className="rounded-sm transition-colors duration-[120ms] enabled:hover:brightness-95
                         disabled:cursor-default"
            >
              <Badge tone="neutral">
                <FileText size={10} aria-hidden="true" />
                <span className="max-w-44 truncate">{d.title}</span>
              </Badge>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AssistantMessage({
  message,
  streaming = false,
  onRetry,
}: {
  message: Msg;
  streaming?: boolean;
  /** Offered when the turn produced nothing; there is nothing else to do with it. */
  onRetry?: () => void;
}) {
  const citations = message.citations ?? [];

  /**
   * A finished turn with nothing in it: no prose, no summary, no draft, and not an
   * abstention. Citations are deliberately not counted as content — a turn can
   * collect sources and still end with no answer, and that is the failure rather
   * than a mitigation of it.
   *
   * Guarded on `!streaming` because every turn passes through this state on its way
   * to its first token.
   */
  const empty =
    !streaming &&
    !message.abstained &&
    !message.summary &&
    !message.draft &&
    !message.content.trim();

  return (
    <div className="group/msg py-5">
      <div className="mb-2.5 flex items-center gap-2">
        {message.intent && <IntentBadge intent={message.intent} />}
        {message.grounded && !message.abstained && !empty && citations.length > 0 && (
          <Badge tone="grounded">
            <ShieldCheck size={11} />
            {citations.length} {citations.length === 1 ? "source" : "sources"}
          </Badge>
        )}
      </div>

      {message.abstained && <AbstentionNotice />}
      {empty && <IncompleteNotice onRetry={onRetry} />}

      {message.summary ? (
        <SummaryCard fields={message.summary} citations={citations} />
      ) : message.draft ? (
        <DraftDocument
          draft={message.draft}
          citations={citations}
          // Export needs a persisted turn; a streaming one has no row to read yet.
          messageId={streaming ? undefined : message.id}
        />
      ) : (
        <Markdown content={message.content} citations={citations} streaming={streaming} />
      )}

      {streaming && (
        <span
          className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] rounded-full
                     bg-brand-600 animate-caret"
          aria-hidden="true"
        />
      )}

      {!streaming && !empty && !message.summary && !message.draft && (
        <MessageActions content={message.content} />
      )}
    </div>
  );
}
