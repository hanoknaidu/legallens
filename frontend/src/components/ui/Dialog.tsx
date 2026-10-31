import * as D from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-ink/25 backdrop-blur-[1px]" />
        <D.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2
                     -translate-y-1/2 rounded-lg border border-line bg-surface p-5 shadow-[var(--shadow-2)]"
        >
          <D.Title className="text-section font-semibold text-ink">{title}</D.Title>
          {description && (
            <D.Description className="mt-1 text-ui text-ink-muted">{description}</D.Description>
          )}
          <div className="mt-4">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
