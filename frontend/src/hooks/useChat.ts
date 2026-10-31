import { useCallback, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StreamError, streamAnswer, type StreamMeta } from "@/lib/sse";
import type { Message } from "@/types/domain";

export interface StreamingState {
  id: string;
  content: string;
  meta: StreamMeta | null;
}

/** A turn that failed after the stream opened, kept so the UI can say so. */
export interface ChatFailure {
  message: string;
  requestId?: string;
  /** What was typed, so the turn can be retried without retyping it. */
  question: string;
  scopedDocumentIds: string[];
}

export function useChat(conversationId: string | undefined) {
  const qc = useQueryClient();
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const [pendingUser, setPendingUser] = useState<Message | null>(null);
  const [failure, setFailure] = useState<ChatFailure | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ["messages", conversationId],
    queryFn: () => api.conversations.messages(conversationId!),
    enabled: !!conversationId,
  });

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(null);
    setPendingUser(null);
    void api.chat.stop();
    void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
  }, [conversationId, qc]);

  const dismissFailure = useCallback(() => setFailure(null), []);

  const send = useCallback(
    async (content: string, scopedDocumentIds: string[]) => {
      if (!conversationId || !content.trim()) return;

      // A new attempt clears the last failure, so a retry that works does not leave
      // the notice from the attempt that didn't.
      setFailure(null);

      // Optimistic: the user's turn appears instantly, before any round trip.
      setPendingUser({
        id: `local-${Date.now()}`,
        conversationId,
        role: "user",
        content,
        createdAt: new Date().toISOString(),
        scopedDocumentIds,
      });

      try {
        const { assistantMessageId } = await api.chat.send({
          conversationId,
          content,
          scopedDocumentIds,
        });

        const controller = new AbortController();
        abortRef.current = controller;
        setStreaming({ id: assistantMessageId, content: "", meta: null });

        await streamAnswer(
          assistantMessageId,
          {
            onMeta: (meta) => setStreaming((s) => (s ? { ...s, meta } : s)),
            onToken: (text) =>
              setStreaming((s) => (s ? { ...s, content: s.content + text } : s)),
            onDone: () => {
              setStreaming(null);
              setPendingUser(null);
              abortRef.current = null;
              void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
              void qc.invalidateQueries({ queryKey: ["conversations"] });
            },
            onError: (err) => {
              setStreaming(null);
              setPendingUser(null);
              // The turn is persisted server-side even when it failed, so the
              // transcript is refetched: whatever text arrived before the failure is
              // what the attorney saw, and the notice sits beside it.
              void qc.invalidateQueries({ queryKey: ["messages", conversationId] });
              setFailure({
                message:
                  err instanceof StreamError
                    ? err.message
                    : "The connection to the server was lost.",
                requestId: err instanceof StreamError ? err.requestId : undefined,
                question: content,
                scopedDocumentIds,
              });
            },
          },
          controller.signal,
        );
      } catch {
        setStreaming(null);
        setPendingUser(null);
        // The send itself failed, so no turn exists server-side and nothing was
        // streamed — but the attorney still typed a question and is owed an answer
        // about why it vanished.
        setFailure({
          message: "The message could not be sent.",
          question: content,
          scopedDocumentIds,
        });
      }
    },
    [conversationId, qc],
  );

  return {
    messages,
    isLoading,
    streaming,
    pendingUser,
    failure,
    send,
    stop,
    dismissFailure,
  };
}
