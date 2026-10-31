import { http, HttpResponse, delay } from "msw";
import { convStore, msgStore } from "./conversations";
import { messagesByConversation } from "../fixtures/messages";
import { chunkAnswer, delayFor, sleep } from "../stream";
import type { Intent, Message } from "@/types/domain";

/** Mirrors what the real Haiku 4.5 intent router will return. */
function detectIntent(text: string): Intent {
  if (/\b(summar|summaris|summariz|key terms|overview of)\b/i.test(text)) return "summary";
  if (/\b(draft|memo|memorandum|write me|prepare a)\b/i.test(text)) return "draft";
  return "ask";
}

/** Pick a canned assistant turn that matches the question. */
function answerFor(text: string, intent: Intent): Message {
  const pick = (id: string) =>
    messagesByConversation[id].find((m) => m.role === "assistant")!;
  if (intent === "summary") return pick("c-summary");
  if (intent === "draft") return pick("c-draft");
  if (/\binsur|coverage|certificate\b/i.test(text)) return pick("c-insure");
  if (/\bindemnif|liabilit|cap\b/i.test(text)) return pick("c-indem");
  if (/\bgovern|venue|jurisdiction|law\b/i.test(text)) return pick("c-venue");
  return pick("c-term");
}

const pending = new Map<string, Message>();

export const chatHandlers = [
  http.post("/api/chat/message", async ({ request }) => {
    await delay(160);
    const body = (await request.json()) as {
      conversationId: string;
      content: string;
      scopedDocumentIds?: string[];
    };
    const intent = detectIntent(body.content);
    const now = new Date().toISOString();

    const userMsg: Message = {
      id: `u-${Math.random().toString(36).slice(2, 9)}`,
      conversationId: body.conversationId,
      role: "user",
      content: body.content,
      createdAt: now,
      scopedDocumentIds: body.scopedDocumentIds,
    };
    (msgStore[body.conversationId] ??= []).push(userMsg);

    const template = answerFor(body.content, intent);
    const assistantId = `a-${Math.random().toString(36).slice(2, 9)}`;
    pending.set(assistantId, {
      ...template,
      id: assistantId,
      conversationId: body.conversationId,
      createdAt: new Date().toISOString(),
      intent,
    });

    const conv = convStore.find((c) => c.id === body.conversationId);
    if (conv) {
      conv.updatedAt = now;
      conv.lastIntent = intent;
      if (conv.title === "New conversation") {
        conv.title = body.content.length > 52 ? `${body.content.slice(0, 52)}…` : body.content;
      }
    }

    return HttpResponse.json({ userMessage: userMsg, assistantMessageId: assistantId, intent });
  }),

  /**
   * SSE over fetch (not EventSource) — EventSource isn't interceptable by MSW,
   * and fetch gives us an AbortSignal, which is what the stop button needs.
   */
  http.get("/api/chat/stream", ({ request }) => {
    const id = new URL(request.url).searchParams.get("messageId") ?? "";
    const msg = pending.get(id);
    if (!msg) return new HttpResponse(null, { status: 404 });

    const encoder = new TextEncoder();
    const send = (c: ReadableStreamDefaultController, event: string, data: unknown) =>
      c.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));

    const stream = new ReadableStream({
      async start(controller) {
        try {
          send(controller, "meta", {
            id: msg.id,
            intent: msg.intent,
            grounded: msg.grounded,
            abstained: msg.abstained,
            citations: msg.citations ?? [],
            summary: msg.summary,
            draft: msg.draft,
          });

          // Structured payloads (summary, draft) resolve as a unit rather than
          // streaming word by word — a half-rendered table reads as broken.
          if (msg.summary || msg.draft) {
            await sleep(900);
          } else {
            const chunks = chunkAnswer(msg.content);
            for (let i = 0; i < chunks.length; i++) {
              await sleep(delayFor(chunks[i], i));
              send(controller, "token", { text: chunks[i] });
            }
          }

          send(controller, "done", { id: msg.id });
          pending.delete(id);
          const stored: Message = { ...msg };
          (msgStore[msg.conversationId] ??= []).push(stored);
        } finally {
          controller.close();
        }
      },
    });

    return new HttpResponse(stream, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }),

  http.post("/api/chat/stop", async () => {
    await delay(60);
    return new HttpResponse(null, { status: 204 });
  }),
];
