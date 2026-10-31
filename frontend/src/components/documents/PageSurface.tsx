import { AlertTriangle } from "lucide-react";
import { HighlightedText, findSpan } from "./Highlight";
import type { DocumentPage } from "@/types/domain";

/**
 * One page of a document, rendered as a page.
 *
 * It renders the extracted **text layer**, which is the same thing the model was shown
 * and the same thing a citation resolves against. That is the property that matters for
 * trust: what the reader verifies is what the answer was grounded in, not a separate
 * rendering of it that might differ.
 *
 * Zoom is applied as a font size on the sheet and everything inside is sized in `em`, so
 * one number scales the whole page and no layout breaks at the extremes.
 */
export function PageSurface({
  page,
  totalPages,
  spans,
  activeSpan,
  zoom,
}: {
  page: DocumentPage;
  totalPages: number;
  spans: string[];
  activeSpan?: string | null;
  zoom: number;
}) {
  const blocks = page.text.split(/\n{2,}/);
  const missing = activeSpan != null && !findSpan(page.text, activeSpan);

  return (
    <div className="px-4 py-5">
      {missing && (
        <div
          className="mx-auto mb-3 flex max-w-[46em] items-start gap-2 rounded-md border
                     border-attention-br bg-attention-bg px-3 py-2"
          role="status"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-attention-fg" />
          <p className="text-caption leading-relaxed text-attention-fg">
            The cited passage was not found in this page's text. The page is shown unmarked rather
            than approximately marked.
          </p>
        </div>
      )}

      <article
        className="mx-auto max-w-[46em] rounded-md border border-line bg-surface
                   px-[3.5em] py-[3em] font-serif text-ink shadow-[var(--shadow-1)]"
        style={{ fontSize: `${(zoom * 0.9375).toFixed(4)}rem` }}
        aria-label={`Page ${page.page} of ${totalPages}`}
      >
        {blocks.map((block, i) => (
          <p
            key={i}
            className="mb-[1.15em] whitespace-pre-wrap text-[1em] leading-[1.7] last:mb-0"
          >
            <HighlightedText
              text={block}
              spans={spans}
              activeSpan={activeSpan}
              // Only the block holding the active span needs to scroll itself into view.
              scrollToActive
            />
          </p>
        ))}

        <footer className="mt-[2.5em] border-t border-line pt-[1em] text-center">
          <span className="font-mono text-[0.7em] text-ink-subtle">
            Page {page.page} of {totalPages}
          </span>
        </footer>
      </article>
    </div>
  );
}
