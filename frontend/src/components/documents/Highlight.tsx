import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * Finds cited spans in a page's text and marks them.
 *
 * Matching is whitespace-tolerant: the span as the model cited it may have been a single
 * line, while the page wraps it across several. Collapsing every run of whitespace in the
 * pattern to `\s+` is what makes "the citation highlights nothing" stop being the common
 * case — and an exact `indexOf` would be that case most of the time.
 *
 * It deliberately does not fall back to fuzzy or partial matching. A highlight that is
 * *nearly* the cited text is worse than none: it would assert that the document says
 * something it may not, which is the failure this product exists to prevent. When the
 * span cannot be found, the viewer says so (see PageSurface).
 */

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A pattern that matches the span regardless of how the page wraps or spaces it. */
export function spanPattern(span: string): RegExp {
  const body = span
    .trim()
    .split(/\s+/)
    .map(escapeRe)
    // Typographic substitution is routine between an extracted text layer and a quote.
    .map((word) => word.replace(/['‘’]/g, "['‘’]").replace(/["“”]/g, '["“”]'))
    .join("\\s+");
  return new RegExp(body, "gi");
}

export function findSpan(text: string, span: string): { start: number; end: number } | null {
  const match = spanPattern(span).exec(text);
  return match ? { start: match.index, end: match.index + match[0].length } : null;
}

interface Segment {
  text: string;
  active: boolean;
  cited: boolean;
}

/** Split text into plain / cited / active-cited runs, without overlapping marks. */
function segment(text: string, spans: string[], activeSpan: string | null): Segment[] {
  const ranges: Array<{ start: number; end: number; active: boolean }> = [];

  for (const span of spans) {
    const found = findSpan(text, span);
    if (!found) continue;
    const active = activeSpan != null && span === activeSpan;
    // Keep the longest range when two citations overlap, and let active win ties, so a
    // span is never marked twice or half-marked.
    const clash = ranges.findIndex((r) => found.start < r.end && r.start < found.end);
    if (clash === -1) ranges.push({ ...found, active });
    else if (active || found.end - found.start > ranges[clash].end - ranges[clash].start) {
      ranges[clash] = { ...found, active: active || ranges[clash].active };
    }
  }

  ranges.sort((a, b) => a.start - b.start);

  const out: Segment[] = [];
  let cursor = 0;
  for (const r of ranges) {
    if (r.start > cursor) out.push({ text: text.slice(cursor, r.start), active: false, cited: false });
    out.push({ text: text.slice(r.start, r.end), active: r.active, cited: true });
    cursor = r.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), active: false, cited: false });
  return out;
}

export function HighlightedText({
  text,
  spans,
  activeSpan = null,
  scrollToActive = true,
}: {
  text: string;
  spans: string[];
  activeSpan?: string | null;
  scrollToActive?: boolean;
}) {
  const activeRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!scrollToActive || !activeRef.current) return;
    activeRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeSpan, text, scrollToActive]);

  const segments = segment(text, spans, activeSpan);

  return (
    <>
      {segments.map((seg, i) =>
        seg.cited ? (
          <mark
            key={i}
            ref={seg.active ? activeRef : undefined}
            // `-mx-0.5 px-0.5` so the tint reads as a highlighter stroke over the words
            // rather than a box that changes the line's rhythm.
            className={cn(
              "-mx-0.5 rounded-xs px-0.5 text-inherit",
              seg.active
                ? "bg-cite-bg-active shadow-[inset_0_-2px_0_var(--color-cite-line)]"
                : "bg-cite-bg",
            )}
          >
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}
