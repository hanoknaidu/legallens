import { API_BASE } from "./constants";
import type {
  Citation,
  Conversation,
  DocumentPage,
  HealthResponse,
  Intent,
  LegalDocument,
  Matter,
  Message,
} from "@/types/domain";

/**
 * The single boundary between the UI and the server.
 *
 * Nothing above this file knows the data is mocked. Pointing at the real
 * FastAPI backend means changing API_BASE and not registering the MSW worker.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw await ApiError.fromResponse(res, `${init?.method ?? "GET"} ${path}`);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** The server's problem+json body (backend §5.8). */
interface Problem {
  title?: string;
  detail?: string;
  code?: string;
  requestId?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** A stable string to branch on — e.g. `document_not_ready`. */
    public code?: string,
    /**
     * The server's explanation, written to be shown to a user: the reason a scan was
     * rejected, the name of the document that is not ready. Prefer this over
     * `message` anywhere a person will read it.
     */
    public detail?: string,
    /** Ties a reported failure to the log line that explains it. */
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /**
   * Reads the problem body before discarding the response.
   *
   * The server goes to the trouble of explaining itself — the FR-2 rejection reason,
   * the 409 that names the document still processing — and a client that throws that
   * away leaves the UI with nothing to say but "something went wrong".
   */
  static async fromResponse(res: Response, context: string): Promise<ApiError> {
    let problem: Problem = {};
    try {
      // Not every failure is ours: a proxy or a dev-server error page is HTML, and
      // parsing it must not replace the real status with a parse error.
      if (res.headers.get("content-type")?.includes("json")) {
        problem = (await res.json()) as Problem;
      }
    } catch {
      // Keep the status; the body was unreadable and is not worth a second failure.
    }
    return new ApiError(
      res.status,
      problem.detail ?? problem.title ?? `${context} failed (${res.status})`,
      problem.code,
      problem.detail,
      problem.requestId,
    );
  }
}

/**
 * Downloads a generated turn as a file.
 *
 * Deliberately not routed through `request()`: that parses JSON, and this endpoint
 * returns document bytes. The filename comes from `Content-Disposition` so the server
 * owns the naming, with a fallback for the case where a proxy strips the header.
 */
export async function exportMessage(
  messageId: string,
  format: "docx" | "markdown",
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${API_BASE}/messages/${messageId}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  if (!res.ok) throw await ApiError.fromResponse(res, `POST /messages/${messageId}/export`);

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return {
    blob: await res.blob(),
    filename: match?.[1] ?? `legal-lens-export.${format === "docx" ? "docx" : "md"}`,
  };
}

/** Hands the blob to the browser as a download, then releases the object URL. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  // Revoking immediately can cancel the download in some browsers; one tick is enough.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  matters: () => request<Matter[]>("/matters"),

  conversations: {
    list: () => request<Conversation[]>("/conversations"),
    /** `title` is required by the server; pass `DEFAULT_CONVERSATION_TITLE` for an unnamed one. */
    create: (body: { title: string; matterId?: string }) =>
      request<Conversation>("/conversations", { method: "POST", body: JSON.stringify(body) }),
    messages: (id: string) => request<Message[]>(`/conversations/${id}/messages`),
    rename: (id: string, title: string) =>
      request<Conversation>(`/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      }),
    remove: (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
  },

  chat: {
    send: (body: { conversationId: string; content: string; scopedDocumentIds?: string[] }) =>
      request<{ userMessage: Message; assistantMessageId: string; intent: Intent }>(
        "/chat/message",
        { method: "POST", body: JSON.stringify(body) },
      ),
    stop: () => request<void>("/chat/stop", { method: "POST" }),
    citations: (messageId: string) =>
      request<{ messageId: string; citations: Citation[] }>(`/chat/${messageId}/citations`),
  },

  documents: {
    list: (matterId?: string) =>
      request<LegalDocument[]>(`/documents${matterId ? `?matterId=${matterId}` : ""}`),
    get: (id: string) => request<LegalDocument>(`/documents/${id}`),
    status: (id: string) =>
      request<{ id: string; status: LegalDocument["status"]; failureReason?: string }>(
        `/documents/${id}/status`,
      ),
    /** One page of the text layer. The viewer renders this and highlights spans in it. */
    page: (id: string, page: number) =>
      request<DocumentPage>(`/documents/${id}/page/${page}`),
    /**
     * Sends the file itself, as multipart.
     *
     * `headers: {}` is load-bearing: it clears the JSON content type so the browser
     * sets `multipart/form-data` *with its own boundary*. Leaving the default header in
     * place produces a body the server cannot parse.
     */
    upload: (file: File, matterId: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("matterId", matterId);
      return request<LegalDocument>("/documents", {
        method: "POST",
        body: form,
        headers: {},
      });
    },
    remove: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  },
};
