import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { DAY_GROUP_ORDER, relativeDay } from "@/lib/format";
import { useUIStore } from "@/stores/useUIStore";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { ConversationItem } from "./ConversationItem";
import type { Conversation } from "@/types/domain";

export function ConversationList() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { conversationId } = useParams();
  const activeMatterId = useUIStore((s) => s.activeMatterId);
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  const { data: conversations, isLoading } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations.list,
  });

  /**
   * Both mutations are optimistic with a rollback.
   *
   * The sidebar is the one surface where latency is most visible — a rename that takes a
   * beat to appear reads as a lost keystroke, and a delete that lingers gets clicked
   * twice. `onMutate` writes the cache, `onError` puts back exactly what was there, and
   * `onSettled` reconciles with the server either way.
   */
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.conversations.rename(id, title),
    onMutate: async ({ id, title }) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = qc.getQueryData<Conversation[]>(["conversations"]);
      qc.setQueryData<Conversation[]>(["conversations"], (list) =>
        (list ?? []).map((c) => (c.id === id ? { ...c, title } : c)),
      );
      return { previous };
    },
    onError: (_e, _vars, context) => qc.setQueryData(["conversations"], context?.previous),
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.conversations.remove(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = qc.getQueryData<Conversation[]>(["conversations"]);
      qc.setQueryData<Conversation[]>(["conversations"], (list) =>
        (list ?? []).filter((c) => c.id !== id),
      );
      // Navigate only once the row is gone, or the redirect races the list update and
      // the deleted thread flashes back in.
      if (id === conversationId) navigate("/chat/new");
      return { previous };
    },
    onError: (_e, _id, context) => qc.setQueryData(["conversations"], context?.previous),
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const groups = useMemo(() => {
    const filtered = (conversations ?? [])
      .filter((c) => !activeMatterId || c.matterId === activeMatterId)
      .filter((c) => c.title.toLowerCase().includes(query.trim().toLowerCase()));
    const map = new Map<string, Conversation[]>();
    for (const c of filtered) {
      const key = relativeDay(c.updatedAt);
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    return DAY_GROUP_ORDER.filter((k) => map.has(k)).map((k) => [k, map.get(k)!] as const);
  }, [conversations, query, activeMatterId]);

  return (
    <>
      <div className="px-2 pb-2">
        <div className="relative">
          <Search
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-subtle"
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search conversations"
            aria-label="Search conversations"
            className="h-8 bg-transparent pl-8 text-small"
          />
        </div>
      </div>

      <nav aria-label="Conversations" className="flex-1 overflow-y-auto px-2 pb-4">
        {isLoading && (
          <div className="space-y-1.5 px-1 pt-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        )}

        {!isLoading && groups.length === 0 && (
          <p className="px-1 pt-3 text-caption text-ink-subtle">
            {query ? `No conversations match “${query}”.` : "No conversations yet."}
          </p>
        )}

        {groups.map(([label, items]) => (
          <section key={label} className="mb-3">
            <h3 className="sticky top-0 z-10 bg-canvas/95 px-1 py-1.5 text-micro font-medium
                           uppercase tracking-wider text-ink-subtle backdrop-blur-sm">
              {label}
            </h3>
            <ul className="space-y-0.5">
              {items.map((c) => (
                <li key={c.id}>
                  <ConversationItem
                    conversation={c}
                    onRename={(conv) => {
                      setRenaming(conv);
                      setDraftTitle(conv.title);
                    }}
                    onDelete={setDeleting}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>

      <Dialog
        open={!!renaming}
        onOpenChange={(o) => !o && setRenaming(null)}
        title="Rename conversation"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (renaming && draftTitle.trim()) {
              rename.mutate({ id: renaming.id, title: draftTitle.trim() });
            }
            setRenaming(null);
          }}
        >
          <Input
            autoFocus
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            aria-label="Conversation title"
          />
          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary">
              Save
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete conversation?"
        description={`“${deleting?.title ?? ""}” and its messages will be removed. This cannot be undone.`}
      >
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (deleting) remove.mutate(deleting.id);
              setDeleting(null);
            }}
          >
            Delete
          </Button>
        </div>
      </Dialog>
    </>
  );
}
