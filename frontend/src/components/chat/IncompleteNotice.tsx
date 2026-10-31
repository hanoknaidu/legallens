import { AlertCircle } from "lucide-react";

/**
 * Rendered when a turn finished with nothing in it.
 *
 * Distinct from `AbstentionNotice`, and the distinction is the whole point. An
 * abstention is the product working: the documents were read and they do not answer
 * the question. This is the product failing: the generation ended before it produced
 * anything, and whether the documents answer the question is still unknown.
 *
 * Styled as a failure for that reason — the one thing this must never do is read like
 * "the documents are silent", which is a claim nobody has established.
 *
 * It replaces a blank bubble under an intent badge, which is what the transcript
 * showed before: a reader had no way to tell a failed turn from an answer that had
 * somehow rendered as nothing, and no reason to retry.
 */
export function IncompleteNotice({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      role="status"
      className="flex items-start gap-2 rounded-md border border-risk-br bg-risk-bg px-3 py-2.5"
    >
      <AlertCircle size={15} className="mt-0.5 shrink-0 text-risk-fg" />
      <div className="min-w-0">
        <p className="text-caption text-risk-fg">
          <span className="font-medium">This answer did not complete.</span> Nothing was
          generated, so nothing here has been checked against the documents.
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-1.5 text-caption font-medium text-risk-fg underline underline-offset-2
                       hover:no-underline"
          >
            Ask again
          </button>
        )}
      </div>
    </div>
  );
}
