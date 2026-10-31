import { SearchX } from "lucide-react";

/**
 * Rendered when the documents don't support an answer.
 *
 * Deliberately NOT styled as an error: under BRD NFR-1 this is the product
 * working correctly, and styling it like a failure would teach users to
 * distrust the one behaviour that makes the tool safe to rely on.
 */
export function AbstentionNotice() {
  return (
    <div className="mb-3 flex items-center gap-2 rounded-md border border-line bg-sunken px-3 py-2">
      <SearchX size={15} className="shrink-0 text-ink-muted" />
      <p className="text-caption text-ink-muted">
        <span className="font-medium text-ink">Not found in the documents in scope.</span>{" "}
        No answer was inferred.
      </p>
    </div>
  );
}
