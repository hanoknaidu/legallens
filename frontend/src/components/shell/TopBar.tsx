import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PanelLeft, PanelRight } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useUIStore } from "@/stores/useUIStore";
import { useDocPanelStore } from "@/stores/useDocPanelStore";

/**
 * The title is editable in place when `onRename` is supplied.
 *
 * Renaming a conversation from a dialog is one dialog too many for a two-word edit: the
 * title is where it already is, it just becomes typeable. Escape abandons the edit, Enter
 * and blur commit it, and an empty title is refused rather than saved.
 */
function EditableTitle({ title, onRename }: { title: string; onRename: (t: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setDraft(title), [title]);
  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    if (next && next !== title) onRename(next);
    else setDraft(title);
    setEditing(false);
  };

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="-mx-1.5 max-w-full truncate rounded-sm px-1.5 py-0.5 text-left text-ui
                   font-medium text-ink transition-colors duration-[120ms] hover:bg-sunken"
        title="Rename conversation"
      >
        {title}
      </button>
    );
  }

  return (
    <input
      ref={inputRef}
      autoFocus
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        else if (e.key === "Escape") {
          setDraft(title);
          setEditing(false);
        }
      }}
      aria-label="Conversation title"
      className="-mx-1.5 w-full rounded-sm border border-brand-500 bg-surface px-1.5 py-0.5
                 text-ui font-medium text-ink focus:outline-none"
    />
  );
}

export function TopBar({
  title,
  meta,
  onRename,
  showPanelToggle = false,
}: {
  title: string;
  meta?: React.ReactNode;
  onRename?: (title: string) => void;
  showPanelToggle?: boolean;
}) {
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const { open, documentId, openDocument, close } = useDocPanelStore();

  // Only needed to answer "which document should the toggle open first?" — the query is
  // already in cache from the library and the composer's scope picker.
  const { data: documents = [] } = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.documents.list(),
    enabled: showPanelToggle,
  });

  const fallback = documents.find((d) => d.status === "ready");
  const canOpenPanel = !!documentId || !!fallback;

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      <Tooltip content="Toggle sidebar  ⌘B">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="Toggle sidebar">
          <PanelLeft size={16} />
        </Button>
      </Tooltip>

      <div className="min-w-0 flex-1">
        {onRename ? (
          <h1 className="min-w-0 truncate">
            <EditableTitle title={title} onRename={onRename} />
          </h1>
        ) : (
          <h1 className="truncate text-ui font-medium text-ink">{title}</h1>
        )}
      </div>

      {meta}

      {showPanelToggle && (
        <Tooltip content={open ? "Hide sources" : "Show sources"}>
          <Button
            variant="ghost"
            size="icon"
            disabled={!canOpenPanel}
            aria-pressed={open}
            aria-label={open ? "Hide document panel" : "Show document panel"}
            onClick={() => {
              if (open) close();
              else if (documentId) openDocument(documentId);
              else if (fallback) openDocument(fallback.id);
            }}
            className={cn(open && "bg-brand-50 text-brand-700")}
          >
            <PanelRight size={16} />
          </Button>
        </Tooltip>
      )}
    </header>
  );
}
