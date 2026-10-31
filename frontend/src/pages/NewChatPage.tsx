import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DEFAULT_CONVERSATION_TITLE } from "@/lib/constants";
import { useUIStore } from "@/stores/useUIStore";
import { TopBar } from "@/components/shell/TopBar";
import { Composer } from "@/components/composer/Composer";
import { EmptyState } from "@/components/chat/EmptyState";

/**
 * A conversation isn't created until there's something to put in it — an empty
 * thread in the sidebar before the user has typed anything is just clutter.
 */
export function NewChatPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const activeMatterId = useUIStore((s) => s.activeMatterId);
  const [draft, setDraft] = useState("");
  const [scopedIds, setScopedIds] = useState<string[]>([]);

  const start = useMutation({
    mutationFn: async (content: string) => {
      const conv = await api.conversations.create({
        title: DEFAULT_CONVERSATION_TITLE,
        matterId: activeMatterId ?? "m-001",
      });
      return { conv, content };
    },
    onSuccess: ({ conv, content }) => {
      void qc.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chat/${conv.id}`, { state: { initialMessage: content, scopedIds } });
    },
  });

  return (
    <>
      <TopBar title="New conversation" />
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto">
          <EmptyState onPick={(p) => start.mutate(p)} />
        </div>
        <Composer
          value={draft}
          onValueChange={setDraft}
          onSend={(text) => start.mutate(text)}
          onStop={() => {}}
          streaming={start.isPending}
          scopedIds={scopedIds}
          onScopeChange={setScopedIds}
        />
      </div>
    </>
  );
}
