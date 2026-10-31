import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  width: number;
  onWidthChange: (width: number) => void;
  min: number;
  max: number;
  /** The panel is on the right, so dragging left widens it. */
  edge?: "left" | "right";
  step?: number;
}

/**
 * Drag-to-resize that is also operable from the keyboard.
 *
 * A mouse-only resize handle is a WCAG 2.1 AA failure (NFR-12), and the fix is cheaper
 * than the audit that finds it: the handle is a focusable `separator` whose value the
 * arrow keys change. Pointer events (not mouse events) so a touch drag works too.
 */
export function useResizablePanel({
  width,
  onWidthChange,
  min,
  max,
  edge = "left",
  step = 24,
}: Options) {
  const [dragging, setDragging] = useState(false);
  const startRef = useRef({ x: 0, width });

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      e.preventDefault();
      startRef.current = { x: e.clientX, width };
      setDragging(true);
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [width],
  );

  useEffect(() => {
    if (!dragging) return;

    const onMove = (e: PointerEvent) => {
      const delta = edge === "left" ? startRef.current.x - e.clientX : e.clientX - startRef.current.x;
      onWidthChange(Math.min(max, Math.max(min, startRef.current.width + delta)));
    };
    const onUp = () => setDragging(false);

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    // Without this a drag over the page selects text the whole way across.
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = "";
    };
  }, [dragging, edge, max, min, onWidthChange]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLElement>) => {
      const grow = edge === "left" ? "ArrowLeft" : "ArrowRight";
      const shrink = edge === "left" ? "ArrowRight" : "ArrowLeft";
      if (e.key === grow) onWidthChange(Math.min(max, width + step));
      else if (e.key === shrink) onWidthChange(Math.max(min, width - step));
      else if (e.key === "Home") onWidthChange(min);
      else if (e.key === "End") onWidthChange(max);
      else return;
      e.preventDefault();
    },
    [edge, max, min, onWidthChange, step, width],
  );

  /** Spread onto the handle: it is a real separator with a value, not a styled div. */
  const handleProps = {
    role: "separator" as const,
    tabIndex: 0,
    "aria-orientation": "vertical" as const,
    "aria-label": "Resize document panel",
    "aria-valuenow": width,
    "aria-valuemin": min,
    "aria-valuemax": max,
    onPointerDown,
    onKeyDown,
  };

  return { dragging, handleProps };
}
