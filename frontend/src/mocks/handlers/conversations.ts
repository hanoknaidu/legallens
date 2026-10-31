import { http, HttpResponse, delay } from "msw";
import { conversations } from "../fixtures/conversations";
import { messagesByConversation } from "../fixtures/messages";
import type { Conversation, Message } from "@/types/domain";

export const convStore: Conversation[] = [...conversations];
export const msgStore: Record<string, Message[]> = structuredClone(messagesByConversation);

export const conversationHandlers = [
  http.get("/api/conversations", async () => {
    await delay(180);
    const sorted = [...convStore].sort(
      (a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt),
    );
    return HttpResponse.json(sorted);
  }),

  http.post("/api/conversations", async ({ request }) => {
    await delay(140);
    const body = (await request.json().catch(() => ({}))) as Partial<Conversation>;
    // The real route requires a title and 422s without one. Mirrored here so the
    // mock cannot pass a request the backend would reject.
    if (!body.title?.trim()) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Request is not valid",
          status: 422,
          detail: "title: String should have at least 1 character",
          code: "validation_error",
        },
        { status: 422, headers: { "Content-Type": "application/problem+json" } },
      );
    }
    const now = new Date().toISOString();
    const conv: Conversation = {
      id: `c-${Math.random().toString(36).slice(2, 9)}`,
      title: body.title.trim(),
      matterId: body.matterId ?? "m-001",
      createdAt: now,
      updatedAt: now,
    };
    convStore.unshift(conv);
    msgStore[conv.id] = [];
    return HttpResponse.json(conv, { status: 201 });
  }),

  http.get("/api/conversations/:id/messages", async ({ params }) => {
    await delay(200);
    return HttpResponse.json(msgStore[params.id as string] ?? []);
  }),

  http.patch("/api/conversations/:id", async ({ params, request }) => {
    await delay(120);
    const body = (await request.json()) as { title?: string };
    const conv = convStore.find((c) => c.id === params.id);
    if (!conv) return new HttpResponse(null, { status: 404 });
    if (body.title) conv.title = body.title;
    conv.updatedAt = new Date().toISOString();
    return HttpResponse.json(conv);
  }),

  http.delete("/api/conversations/:id", async ({ params }) => {
    await delay(150);
    const i = convStore.findIndex((c) => c.id === params.id);
    if (i >= 0) convStore.splice(i, 1);
    delete msgStore[params.id as string];
    return new HttpResponse(null, { status: 204 });
  }),
];
