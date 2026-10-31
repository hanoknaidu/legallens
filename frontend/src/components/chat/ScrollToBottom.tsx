import { ArrowDown } from "lucide-react";

export function ScrollToBottom({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-label="Scroll to latest message"
      className="absolute bottom-4 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center
                 justify-center rounded-full border border-line bg-surface text-ink-muted
                 shadow-[var(--shadow-2)] transition-colors duration-[120ms]
                 hover:border-line-strong hover:text-ink"
    >
      <ArrowDown size={15} />
    </button>
  );
}
