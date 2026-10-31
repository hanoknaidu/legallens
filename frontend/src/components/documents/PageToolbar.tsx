import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { ZOOM_STEPS } from "@/stores/useDocPanelStore";

export function PageToolbar({
  page,
  totalPages,
  zoom,
  onPageChange,
  onZoomChange,
}: {
  page: number;
  totalPages: number;
  zoom: number;
  onPageChange: (page: number) => void;
  onZoomChange: (zoom: number) => void;
}) {
  // The input is a draft until it is committed, or typing "12" would navigate to page 1
  // on the first keystroke.
  const [draft, setDraft] = useState(String(page));
  useEffect(() => setDraft(String(page)), [page]);

  // Nearest step, not exact index: a persisted zoom from an older step list would
  // otherwise read as "already at minimum" and disable both buttons.
  const zoomIndex = ZOOM_STEPS.reduce(
    (best, step, i) => (Math.abs(step - zoom) < Math.abs(ZOOM_STEPS[best] - zoom) ? i : best),
    0,
  );
  const atMinZoom = zoomIndex <= 0;
  const atMaxZoom = zoomIndex >= ZOOM_STEPS.length - 1;

  const commit = () => {
    const n = Number(draft);
    if (Number.isFinite(n) && n >= 1 && n <= totalPages) onPageChange(n);
    else setDraft(String(page));
  };

  return (
    <div className="flex items-center gap-1 border-b border-line bg-sunken px-2 py-1.5">
      <Tooltip content="Previous page">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft size={15} />
        </Button>
      </Tooltip>
      <Tooltip content="Next page">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages}
          aria-label="Next page"
        >
          <ChevronRight size={15} />
        </Button>
      </Tooltip>

      <div className="ml-1 flex items-center gap-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.replace(/[^\d]/g, ""))}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
              e.currentTarget.blur();
            }
          }}
          inputMode="numeric"
          aria-label={`Page number, of ${totalPages}`}
          className="h-6 w-10 rounded-sm border border-line bg-surface text-center font-mono
                     text-caption text-ink transition-colors duration-[120ms]
                     hover:border-line-strong focus:border-brand-500"
        />
        <span className="font-mono text-caption text-ink-subtle">of {totalPages}</span>
      </div>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip content="Zoom out">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onZoomChange(ZOOM_STEPS[Math.max(0, zoomIndex - 1)])}
            disabled={atMinZoom}
            aria-label="Zoom out"
          >
            <ZoomOut size={15} />
          </Button>
        </Tooltip>
        <span className="w-10 text-center font-mono text-caption text-ink-subtle" aria-live="polite">
          {Math.round(zoom * 100)}%
        </span>
        <Tooltip content="Zoom in">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => onZoomChange(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, zoomIndex + 1)])}
            disabled={atMaxZoom}
            aria-label="Zoom in"
          >
            <ZoomIn size={15} />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}
