import { useRef } from "react";
import { FileText, ArrowUpRight } from "lucide-react";
import { Popover } from "@/components/ui/Popover";
import { useDocPanelStore } from "@/stores/useDocPanelStore";
import type { Citation } from "@/types/domain";

/**
 * The trust primitive. Every factual claim carries one of these, and it must
 * always lead somewhere real — never a decorative superscript.
 *
 * Opening the source talks to the panel store directly instead of calling an `onOpen`
 * passed down from the page. A chip can sit four levels deep — inside a table cell,
 * inside a markdown body, inside a draft, inside a message — and threading a callback
 * through all of that would put the document panel into the props of everything in
 * between. `onOpen` stays as an override for the rare caller that needs one.
 */
export function CitationChip({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen?: (c: Citation) => void;
}) {
  const openCitation = useDocPanelStore((s) => s.openCitation);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const open = () => {
    if (onOpen) onOpen(citation);
    // Hand over the chip itself, so closing the panel returns focus here (NFR-12).
    else openCitation(citation, triggerRef.current);
  };

  return (
    <Popover
      side="top"
      // Hover (or focus) previews the passage; the click goes to the panel, where the
      // reader can see it in its page. Preview and verification are different acts.
      activation="hover"
      trigger={
        <button
          ref={triggerRef}
          onClick={open}
          className="mx-0.5 inline-flex h-[1.125rem] min-w-[1.125rem] translate-y-[-1px] items-center
                     justify-center rounded-xs border border-brand-100 bg-brand-50 px-1
                     align-middle font-mono text-micro font-medium text-brand-700
                     transition-colors duration-[120ms] hover:border-brand-500 hover:bg-brand-100"
          aria-label={`Source ${citation.n}: ${citation.documentTitle}, page ${citation.page}`}
        >
          {citation.n}
        </button>
      }
    >
      <div className="flex items-start gap-2 border-b border-line pb-2">
        <FileText size={14} className="mt-0.5 shrink-0 text-ink-subtle" />
        <div className="min-w-0">
          <p className="text-caption font-medium leading-snug text-ink">
            {citation.documentTitle}
          </p>
          <p className="mt-0.5 font-mono text-micro text-ink-subtle">Page {citation.page}</p>
        </div>
      </div>
      <blockquote className="mt-2.5 border-l-2 border-brand-100 pl-2.5 font-serif text-caption
                             leading-relaxed text-ink-muted">
        “{citation.citedText}”
      </blockquote>
      <button
        onClick={open}
        className="mt-2.5 inline-flex items-center gap-1 text-caption font-medium text-brand-600
                   hover:text-brand-700 hover:underline"
      >
        Open at page {citation.page}
        <ArrowUpRight size={12} />
      </button>
    </Popover>
  );
}
