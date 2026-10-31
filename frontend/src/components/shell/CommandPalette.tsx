import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as D from "@radix-ui/react-dialog";
import {
  FileText,
  MessageSquare,
  PanelRight,
  PenSquare,
  Search,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { DEFAULT_CONVERSATION_TITLE } from "@/lib/constants";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/stores/useUIStore";
import { useDocPanelStore } from "@/stores/useDocPanelStore";
import { IngestStatus } from "@/components/documents/IngestStatus";

interface Command {
  id: string;
  label: string;
  group: "Actions" | "Conversations" | "Documents";
  Icon: LucideIcon;
  hint?: string;
  trailing?: React.ReactNode;
  run: () => void;
}

/**
 * ⌘K. One list, flat keyboard model: ↑ ↓ move, Enter runs, Escape closes.
 *
 * Built on Radix Dialog rather than a combobox library because the whole behaviour is
 * forty lines and a dependency would own the part most likely to need changing. The
 * filter is substring, case-insensitive, over the label — nothing fuzzy: in a list this
 * short, fuzzy matching mostly produces confident wrong first results.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const activeMatterId = useUIStore((s) => s.activeMatterId);
  const openDocument = useDocPanelStore((s) => s.openDocument);
  const panelOpen = useDocPanelStore((s) => s.open);
  const closePanel = useDocPanelStore((s) => s.close);

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations.list,
    enabled: open,
  });
  const { data: documents = [] } = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.documents.list(),
    enabled: open,
  });

  const create = useMutation({
    mutationFn: () => api.conversations.create({
        title: DEFAULT_CONVERSATION_TITLE,
        matterId: activeMatterId ?? "m-001",
      }),
    onSuccess: (conv) => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chat/${conv.id}`);
    },
  });

  const commands = useMemo<Command[]>(() => {
    const close = () => onOpenChange(false);
    const act = (fn: () => void) => () => {
      close();
      fn();
    };

    return [
      {
        id: "new-chat",
        label: "New chat",
        group: "Actions",
        Icon: PenSquare,
        hint: "⌘N",
        run: act(() => create.mutate()),
      },
      {
        id: "documents",
        label: "Go to documents",
        group: "Actions",
        Icon: FileText,
        run: act(() => navigate("/documents")),
      },
      {
        id: "settings",
        label: "Go to settings",
        group: "Actions",
        Icon: Settings,
        run: act(() => navigate("/settings")),
      },
      {
        id: "toggle-panel",
        label: panelOpen ? "Hide document panel" : "Show document panel",
        group: "Actions",
        Icon: PanelRight,
        run: act(() => {
          if (panelOpen) closePanel();
          else {
            const first = documents.find((d) => d.status === "ready");
            if (first) openDocument(first.id);
          }
        }),
      },
      ...conversations.slice(0, 20).map<Command>((c) => ({
        id: `conv-${c.id}`,
        label: c.title,
        group: "Conversations",
        Icon: MessageSquare,
        run: act(() => navigate(`/chat/${c.id}`)),
      })),
      ...documents.map<Command>((d) => ({
        id: `doc-${d.id}`,
        label: d.title,
        group: "Documents",
        Icon: FileText,
        trailing: d.status === "ready" ? undefined : <IngestStatus status={d.status} />,
        run: act(() =>
          d.status === "ready" ? openDocument(d.id) : navigate(`/documents/${d.id}`),
        ),
      })),
    ];
  }, [
    closePanel,
    conversations,
    create,
    documents,
    navigate,
    onOpenChange,
    openDocument,
    panelOpen,
  ]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? commands.filter((c) => c.label.toLowerCase().includes(q)) : commands;
  }, [commands, query]);

  // Reset between openings: reopening on the item you ran last time is disorienting.
  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
    }
  }, [open]);

  useEffect(() => setIndex(0), [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${index}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [index]);

  const groups = ["Actions", "Conversations", "Documents"] as const;

  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-ink/25 backdrop-blur-[1px]" />
        <D.Content
          aria-label="Command palette"
          className="fixed left-1/2 top-[12vh] z-50 w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2
                     overflow-hidden rounded-lg border border-line bg-surface shadow-[var(--shadow-2)]"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setIndex((i) => Math.min(matches.length - 1, i + 1));
            else if (e.key === "ArrowUp") setIndex((i) => Math.max(0, i - 1));
            else if (e.key === "Enter") matches[index]?.run();
            else return;
            e.preventDefault();
          }}
        >
          <D.Title className="sr-only">Command palette</D.Title>
          <div className="flex items-center gap-2.5 border-b border-line px-3.5">
            <Search size={16} className="shrink-0 text-ink-subtle" aria-hidden="true" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search conversations, documents and actions"
              aria-label="Search conversations, documents and actions"
              aria-activedescendant={matches[index]?.id}
              className="h-12 w-full bg-transparent text-ui text-ink placeholder:text-ink-subtle
                         focus:outline-none"
            />
          </div>

          <div ref={listRef} role="listbox" className="max-h-[52vh] overflow-y-auto py-1.5">
            {matches.length === 0 && (
              <p className="px-3.5 py-6 text-center text-ui text-ink-muted">
                Nothing matches “{query}”.
              </p>
            )}

            {groups.map((group) => {
              const items = matches.filter((m) => m.group === group);
              if (!items.length) return null;
              return (
                <div key={group} className="mb-1 last:mb-0">
                  <p className="px-3.5 py-1 text-micro font-medium uppercase tracking-wider text-ink-subtle">
                    {group}
                  </p>
                  {items.map((item) => {
                    const i = matches.indexOf(item);
                    return (
                      <button
                        key={item.id}
                        id={item.id}
                        role="option"
                        aria-selected={i === index}
                        data-index={i}
                        onMouseMove={() => setIndex(i)}
                        onClick={item.run}
                        className={cn(
                          "flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-ui",
                          i === index ? "bg-brand-50 text-brand-900" : "text-ink",
                        )}
                      >
                        <item.Icon size={15} className="shrink-0 text-ink-subtle" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {item.trailing}
                        {item.hint && (
                          <kbd className="shrink-0 rounded-xs border border-line bg-sunken px-1.5
                                          py-0.5 font-mono text-micro text-ink-subtle">
                            {item.hint}
                          </kbd>
                        )}
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
