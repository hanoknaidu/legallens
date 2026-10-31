import { API_BASE } from "./constants";
import type { Citation, DraftPayload, Intent, SummaryField } from "@/types/domain";

export interface StreamMeta {
  id: string;
  intent: Intent;
  grounded?: boolean;
  abstained?: boolean;
  citations: Citation[];
  summary?: SummaryField[];
  draft?: DraftPayload;
}

interface StreamCallbacks {
  onMeta: (meta: StreamMeta) => void;
  onToken: (text: string) => void;
  onDone: () => void;
  onError?: (err: unknown) => void;
}

/**
 * A generation that failed after the stream opened.
 *
 * The response has already begun by then, so the backend cannot answer with a status
 * code — it sends `event: error` and then `done` (see `api/chat.py`). Until this
 * existed the client had no branch for that frame: it fell through, `done` cleared
 * the spinner, and a failed answer was indistinguishable from a short one.
 */
export class StreamError extends Error {
  readonly requestId?: string;

  constructor(message: string, requestId?: string) {
    super(message);
    this.name = "StreamError";
    this.requestId = requestId;
  }
}

/**
 * Reads an SSE response over fetch so the caller keeps an AbortSignal.
 * EventSource can't be aborted mid-stream, and the stop button needs that.
 */
export async function streamAnswer(
  messageId: string,
  { onMeta, onToken, onDone, onError }: StreamCallbacks,
  signal: AbortSignal,
) {
  try {
    const res = await fetch(`${API_BASE}/chat/stream?messageId=${messageId}`, { signal });
    if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let failure: StreamError | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const eventLine = frame.split("\n").find((l) => l.startsWith("event:"));
        const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!eventLine || !dataLine) continue;
        const event = eventLine.slice(6).trim();
        const data = JSON.parse(dataLine.slice(5).trim());
        if (event === "meta") onMeta(data as StreamMeta);
        else if (event === "token") onToken((data as { text: string }).text);
        else if (event === "error") {
          // `done` always follows, so this is recorded and the loop continues
          // rather than returning — releasing the reader here would leave the
          // response body open.
          const payload = data as { message?: string; requestId?: string };
          failure = new StreamError(
            payload.message ?? "Generation failed before it completed.",
            payload.requestId,
          );
        } else if (event === "done") {
          // The failure wins: a turn that errored did not finish, whatever the
          // protocol's closing frame says.
          if (failure) onError?.(failure);
          else onDone();
        }
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return; // user pressed stop
    onError?.(err);
  }
}
