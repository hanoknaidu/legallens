import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Maximize2, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { layout } from "@/lib/tokens";
import { useDocPanelStore } from "@/stores/useDocPanelStore";
import { useResizablePanel } from "@/hooks/useResizablePanel";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { Skeleton } from "@/components/ui/Skeleton";
import { IngestStatus } from "./IngestStatus";
import { RejectedScanNotice } from "./RejectedScanNotice";
import { PageSurface } from "./PageSurface";
import { PageToolbar } from "./PageToolbar";

/**
 * The source panel — the other half of a citation.
 *
 * A chip that cannot be verified is decoration, so this panel is not a convenience: it is
 * the second half of the product's only hard promise. It opens at the cited page with the
 * cited span marked, and it says so plainly when it cannot mark it.
 *
 * Docked beside the thread above 1440px, an overlay below it (§3.4). Overlaid, it is a
 * dialog in every respect except the implementation — focus moves in, Escape closes, and
 * focus returns to the chip that opened it.
 */
export function DocumentPanel() {
  const { open, documentId, page, active, width, zoom, close, setPage, setWidth, setZoom, trigger } =
    useDocPanelStore();
  const docked = useMediaQuery("(min-width: 1440px)");
  const panelRef = useRef<HTMLElement>(null);

  const { dragging, handleProps } = useResizablePanel({
    width,
    onWidthChange: setWidth,
    min: layout.panelMinPx,
    max: layout.panelMaxPx,
  });

  const { data: doc, isLoading: docLoading } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.documents.get(documentId!),
    enabled: !!documentId,
  });

  const ready = doc?.status === "ready";
  const { data: pageData, isLoading: pageLoading } = useQuery({
    queryKey: ["document-page", documentId, page],
    queryFn: () => api.documents.page(documentId!, page),
    enabled: !!documentId && ready,
    // Page text does not change under us; refetching it on focus would only repaint.
    staleTime: Infinity,
  });

  // Focus moves into the panel on open and back to the chip on close. Without the second
  // half, a keyboard user who closes the panel is returned to the top of the document,
  // having lost the answer they were reading.
  //
  // The trigger is held in a ref rather than read from the store at close time: closing
  // clears the store, so by the time this effect sees `open === false` the element it
  // needs to focus would already be gone.
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    if (trigger) returnFocusRef.current = trigger;
    panelRef.current?.focus();
  }, [open, documentId, trigger]);

  useEffect(() => {
    if (open) return;
    returnFocusRef.current?.focus();
    returnFocusRef.current = null;
  }, [open]);

  if (!open || !documentId) return null;

  const totalPages = doc?.pages ?? 1;
  const spans = active ? [active.citedText] : [];

  const body = (
    <>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-2.5">
        <div className="min-w-0 flex-1">
          {docLoading ? (
            <Skeleton className="h-4 w-48" />
          ) : (
            <>
              <h2 className="truncate text-small font-medium text-ink" title={doc?.title}>
                {doc?.title ?? "Document"}
              </h2>
              <p className="truncate font-mono text-micro text-ink-subtle">{doc?.filename}</p>
            </>
          )}
        </div>

        {doc && doc.status !== "ready" && <IngestStatus status={doc.status} />}

        {doc && (
          <Tooltip content="Open full view">
            <Link
              to={`/documents/${doc.id}`}
              aria-label={`Open ${doc.title} in full view`}
              className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-ink-muted
                         transition-colors duration-[120ms] hover:bg-sunken hover:text-ink"
            >
              <Maximize2 size={14} />
            </Link>
          </Tooltip>
        )}
        <Tooltip content="Close panel  Esc">
          <Button variant="ghost" size="icon-sm" onClick={close} aria-label="Close document panel">
            <X size={15} />
          </Button>
        </Tooltip>
      </header>

      {ready && <PageToolbar page={page} totalPages={totalPages} zoom={zoom} onPageChange={setPage} onZoomChange={setZoom} />}

      <div className="min-h-0 flex-1 overflow-y-auto bg-sunken/60">
        {doc?.status === "failed" ? (
          <div className="p-4">
            <RejectedScanNotice reason={doc.failureReason} />
          </div>
        ) : doc && !ready ? (
          <div className="p-4">
            <div className="rounded-lg border border-line bg-surface px-4 py-6 text-center">
              <IngestStatus status={doc.status} size="md" />
              <p className="mt-3 text-ui text-ink-muted">
                This document is still being indexed. Its pages become viewable — and citable — once
                indexing finishes.
              </p>
            </div>
          </div>
        ) : pageLoading || docLoading ? (
          <div className="px-4 py-5">
            <div className="mx-auto max-w-[46em] space-y-3 rounded-md border border-line bg-surface p-10">
              <Skeleton className="h-3.5 w-1/3" />
              {Array.from({ length: 12 }).map((_, i) => (
                <Skeleton key={i} className={cn("h-3.5", i % 4 === 3 ? "w-2/3" : "w-full")} />
              ))}
            </div>
          </div>
        ) : pageData ? (
          <PageSurface
            page={pageData}
            totalPages={totalPages}
            spans={spans}
            activeSpan={active?.citedText ?? null}
            zoom={zoom}
          />
        ) : (
          <p className="px-4 py-6 text-center text-ui text-ink-muted">
            This page could not be loaded.
          </p>
        )}
      </div>

      {active && (
        <footer className="shrink-0 border-t border-line bg-surface px-3 py-2">
          <p className="text-micro font-medium uppercase tracking-wider text-ink-subtle">
            Showing source {active.n}
          </p>
          <p className="mt-0.5 line-clamp-2 font-serif text-caption leading-relaxed text-ink-muted">
            “{active.citedText}”
          </p>
        </footer>
      )}
    </>
  );

  return (
    <>
      {!docked && (
        <div
          className="fixed inset-0 z-40 bg-ink/20 backdrop-blur-[1px]"
          onClick={close}
          aria-hidden="true"
        />
      )}

      <aside
        ref={panelRef}
        tabIndex={-1}
        role={docked ? "complementary" : "dialog"}
        aria-modal={docked ? undefined : true}
        aria-label="Document source"
        style={{ width: docked ? width : undefined }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            close();
          } else if (e.key === "PageDown" && page < totalPages) setPage(page + 1);
          else if (e.key === "PageUp" && page > 1) setPage(page - 1);
          else if (e.key === "Tab" && !docked) {
            // Overlaid, the panel claims modality, and a modal that lets Tab wander
            // behind the backdrop is lying about it.
            const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])',
            );
            if (!focusables?.length) return;
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            if (!e.shiftKey && document.activeElement === last) first.focus();
            else if (e.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current))
              last.focus();
            else return;
          } else return;
          e.preventDefault();
        }}
        className={cn(
          "flex min-h-0 flex-col border-l border-line bg-canvas focus:outline-none",
          docked
            ? "relative shrink-0"
            : "fixed inset-y-0 right-0 z-50 w-[min(44rem,calc(100vw-3rem))] shadow-[var(--shadow-2)]",
        )}
      >
        {docked && (
          <div
            {...handleProps}
            className={cn(
              "absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize",
              "transition-colors duration-[120ms] hover:bg-brand-100",
              "focus-visible:bg-brand-500 focus-visible:outline-none",
              dragging && "bg-brand-500",
            )}
          />
        )}
        {body}
      </aside>
    </>
  );
}
