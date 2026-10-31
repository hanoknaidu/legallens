import { useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useChat } from "@/hooks/useChat";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { useDocPanelUrl } from "@/hooks/useDocPanelUrl";
import { TopBar } from "@/components/shell/TopBar";
import { Composer } from "@/components/composer/Composer";
import { AssistantMessage, UserMessage } from "@/components/chat/Message";
import { EmptyState } from "@/components/chat/EmptyState";
import { GenerationErrorNotice } from "@/components/chat/GenerationErrorNotice";
import { ScrollToBottom } from "@/components/chat/ScrollToBottom";
import { Skeleton } from "@/components/ui/Skeleton";
import type { Conversation, Message } from "@/types/domain";

export function ChatPage() {
  const { conversationId } = useParams();
  const location = useLocation();
  const qc = useQueryClient();
  // `?doc=&page=` ⇄ the document panel, so a cited answer is a shareable link.
  useDocPanelUrl();
  const sentInitial = useRef(false);
  const [draft, setDraft] = useState("");
  const [scopedIds, setScopedIds] = useState<string[]>([]);
  const { messages, isLoading, streaming, pendingUser, failure, send, stop } =
    useChat(conversationId);

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: api.conversations.list,
  });
  const conversation = conversations.find((c) => c.id === conversationId);

  const rename = useMutation({
    mutationFn: (title: string) => api.conversations.rename(conversationId!, title),
    // Optimistic: the title is already on screen, so it should change when it is typed,
    // not when the server gets around to agreeing.
    onMutate: async (title) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = qc.getQueryData<Conversation[]>(["conversations"]);
      qc.setQueryData<Conversation[]>(["conversations"], (list) =>
        (list ?? []).map((c) => (c.id === conversationId ? { ...c, title } : c)),
      );
      return { previous };
    },
    onError: (_e, _title, context) => {
      qc.setQueryData(["conversations"], context?.previous);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const all: Message[] = [...messages, ...(pendingUser ? [pendingUser] : [])];
  const { ref, pinned, onScroll, scrollToBottom } = useAutoScroll<HTMLDivElement>(
    `${all.length}:${streaming?.content.length ?? 0}`,
  );

  // Default the scope to whatever the last turn used, so follow-ups stay in context.
  useEffect(() => {
    const last = [...messages].reverse().find((m) => m.scopedDocumentIds?.length);
    if (last?.scopedDocumentIds) setScopedIds(last.scopedDocumentIds);
  }, [conversationId, messages]);

  // A message typed on /chat/new is handed over through navigation state.
  useEffect(() => {
    const initial = (location.state as { initialMessage?: string; scopedIds?: string[] } | null)
      ?.initialMessage;
    if (!initial || sentInitial.current || !conversationId) return;
    sentInitial.current = true;
    const ids = (location.state as { scopedIds?: string[] }).scopedIds ?? [];
    setScopedIds(ids);
    void send(initial, ids);
    window.history.replaceState({}, "");
  }, [conversationId, location.state, send]);

  // A failure on the very first message leaves no transcript, and the empty state
  // would then render the suggestion prompts over the top of the notice explaining
  // why nothing happened.
  const empty = !isLoading && all.length === 0 && !streaming && !failure;

  return (
    <>
      <TopBar
        title={conversation?.title ?? "New conversation"}
        onRename={conversation ? (title) => rename.mutate(title) : undefined}
        showPanelToggle
      />

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={ref} onScroll={onScroll} className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="mx-auto measure space-y-6 px-6 py-8">
              <Skeleton className="ml-auto h-12 w-2/3" />
              <div className="space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-11/12" />
                <Skeleton className="h-4 w-4/5" />
              </div>
            </div>
          ) : empty ? (
            <EmptyState
              onPick={(p) => {
                setDraft(p);
                void send(p, scopedIds);
              }}
            />
          ) : (
            <div className="mx-auto measure px-6 pb-8 pt-2">
              {all.map((m, i) =>
                m.role === "user" ? (
                  <UserMessage key={m.id} message={m} />
                ) : (
                  <AssistantMessage
                    key={m.id}
                    message={m}
                    // Only reachable from a turn that produced nothing, where the
                    // alternative is retyping the question. The scope comes from the
                    // user's own turn rather than the composer's current one, so a
                    // retry re-asks what was asked, of the documents it was asked of.
                    onRetry={() => {
                      const asked = all
                        .slice(0, i)
                        .reverse()
                        .find((x) => x.role === "user");
                      if (asked) {
                        void send(asked.content, asked.scopedDocumentIds ?? scopedIds);
                      }
                    }}
                  />
                ),
              )}

              {streaming && (
                <div aria-live="polite" aria-atomic="false">
                  <AssistantMessage
                    streaming
                    message={{
                      id: streaming.id,
                      conversationId: conversationId!,
                      role: "assistant",
                      content: streaming.content,
                      createdAt: new Date().toISOString(),
                      intent: streaming.meta?.intent,
                      grounded: streaming.meta?.grounded,
                      abstained: streaming.meta?.abstained,
                      citations: streaming.meta?.citations,
                      summary: streaming.meta?.summary,
                      draft: streaming.meta?.draft,
                    }}
                  />
                </div>
              )}

              {failure && !streaming && (
                <GenerationErrorNotice
                  message={failure.message}
                  requestId={failure.requestId}
                  onRetry={() => void send(failure.question, failure.scopedDocumentIds)}
                />
              )}
            </div>
          )}
        </div>

        {!pinned && !empty && <ScrollToBottom onClick={scrollToBottom} />}

        <Composer
          value={draft}
          onValueChange={setDraft}
          onSend={(text) => void send(text, scopedIds)}
          onStop={stop}
          streaming={!!streaming}
          scopedIds={scopedIds}
          onScopeChange={setScopedIds}
        />
      </div>
    </>
  );
}
