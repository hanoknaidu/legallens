import { AlertTriangle, RotateCcw } from "lucide-react";

/**
 * Rendered when generation failed after the stream had opened.
 *
 * Styled as a failure, unlike `AbstentionNotice` — the distinction is the point.
 * An abstention is the product working; this is the product not working, and the
 * attorney needs to know which one they are looking at before they act on an empty
 * answer. The backend sends `event: error` for this case and always follows it with
 * `done`, so without a notice the spinner simply stopped and the turn looked
 * finished.
 */
export function GenerationErrorNotice({
  message,
  requestId,
  onRetry,
}: {
  message: string;
  requestId?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="mb-3 flex items-start gap-2 rounded-md border border-risk-br bg-risk-bg px-3 py-2"
    >
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-risk-fg" />
      <div className="min-w-0 flex-1">
        <p className="text-caption text-risk-fg">
          <span className="font-medium">This answer did not finish.</span> {message}
        </p>
        {requestId && (
          <p className="mt-0.5 font-mono text-micro text-ink-muted">{requestId}</p>
        )}
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-caption text-risk-fg hover:bg-surface"
        >
          <RotateCcw size={13} />
          Retry
        </button>
      )}
    </div>
  );
}
