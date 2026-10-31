import { useEffect } from "react";

type Handlers = Record<string, (e: KeyboardEvent) => void>;

/** Keys are like "mod+b", "mod+n", "escape". `mod` is ⌘ on macOS, Ctrl elsewhere. */
export function useKeyboardShortcuts(handlers: Handlers) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();
      const combo = mod ? `mod+${key}` : key;
      const handler = handlers[combo];
      if (!handler) return;
      const target = e.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (typing && !mod && key !== "escape") return;
      e.preventDefault();
      handler(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers]);
}
