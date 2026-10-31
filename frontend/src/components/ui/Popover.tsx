import * as P from "@radix-ui/react-popover";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Two modes, because a citation chip needs both behaviours at once:
 *
 *  - `activation="click"` (default) — Radix's own trigger: click toggles.
 *  - `activation="hover"` — the popover previews on hover and on keyboard focus, leaving
 *    the click free to do something else. The trigger becomes an *anchor*, not a trigger,
 *    so Radix never toggles on click and the host component owns what a click means.
 *
 * Hover alone would be a WCAG failure, so focus opens it too, and it closes on Escape and
 * on blur. A short close delay keeps it from flickering shut when the pointer crosses the
 * gap between the chip and the panel.
 */
export function Popover({
  trigger,
  children,
  className,
  side = "top",
  activation = "click",
  openDelay = 120,
  closeDelay = 140,
}: {
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
  activation?: "click" | "hover";
  openDelay?: number;
  closeDelay?: number;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const schedule = (next: boolean, delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), delay);
  };

  const content = (
    <P.Portal>
      <P.Content
        side={side}
        sideOffset={8}
        collisionPadding={16}
        // With hover activation the pointer has to be able to reach the content — and
        // Radix would otherwise steal focus from the element the reader is tabbing through.
        onOpenAutoFocus={activation === "hover" ? (e) => e.preventDefault() : undefined}
        onPointerEnter={activation === "hover" ? () => window.clearTimeout(timer.current) : undefined}
        onPointerLeave={activation === "hover" ? () => schedule(false, closeDelay) : undefined}
        className={cn(
          "z-50 w-80 rounded-md border border-line bg-surface p-3 shadow-[var(--shadow-2)]",
          "focus:outline-none",
          className,
        )}
      >
        {children}
        <P.Arrow className="fill-surface" />
      </P.Content>
    </P.Portal>
  );

  if (activation === "click") {
    return (
      <P.Root>
        <P.Trigger asChild>{trigger}</P.Trigger>
        {content}
      </P.Root>
    );
  }

  return (
    <P.Root open={open} onOpenChange={setOpen}>
      <P.Anchor asChild>
        <span
          className="inline"
          onPointerEnter={() => schedule(true, openDelay)}
          onPointerLeave={() => schedule(false, closeDelay)}
          onFocusCapture={() => setOpen(true)}
          onBlurCapture={() => schedule(false, 0)}
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        >
          {trigger}
        </span>
      </P.Anchor>
      {content}
    </P.Root>
  );
}
