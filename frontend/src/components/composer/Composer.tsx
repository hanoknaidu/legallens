import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Square, FileText, Check, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/Menu";

export function Composer({
  onSend,
  onStop,
  streaming,
  value,
  onValueChange,
  scopedIds,
  onScopeChange,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  value: string;
  onValueChange: (v: string) => void;
  scopedIds: string[];
  onScopeChange: (ids: string[]) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);
  const { data: documents = [] } = useQuery({ queryKey: ["documents"], queryFn: () => api.documents.list() });
  const ready = documents.filter((d) => d.status === "ready");

  // Grow to ~8 rows, then scroll. Resetting height first keeps shrink working.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming) return;
    onSend(text);
    onValueChange("");
  };

  return (
    <div className="border-t border-line bg-canvas px-4 pb-4 pt-3">
      <div className="mx-auto measure">
        {scopedIds.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="text-micro uppercase tracking-wider text-ink-subtle">Scope</span>
            {scopedIds.map((id) => {
              const d = documents.find((x) => x.id === id);
              if (!d) return null;
              return (
                <button
                  key={id}
                  onClick={() => onScopeChange(scopedIds.filter((x) => x !== id))}
                  className="group"
                  aria-label={`Remove ${d.title} from scope`}
                >
                  <Badge tone="brand" size="md">
                    <FileText size={11} />
                    <span className="max-w-52 truncate">{d.title}</span>
                    <span className="text-brand-700/60 group-hover:text-brand-700">×</span>
                  </Badge>
                </button>
              );
            })}
          </div>
        )}

        <div
          className={cn(
            "flex items-end gap-2 rounded-lg border bg-surface px-2.5 py-2 transition-colors duration-[120ms]",
            focused ? "border-brand-500 shadow-[var(--shadow-1)]" : "border-line hover:border-line-strong",
          )}
        >
          <Menu>
            <MenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Add documents to scope" className="shrink-0">
                <Plus size={17} />
              </Button>
            </MenuTrigger>
            <MenuContent align="start">
              {ready.length === 0 && <MenuItem>No indexed documents yet</MenuItem>}
              {ready.map((d) => (
                <MenuItem
                  key={d.id}
                  onSelect={() =>
                    onScopeChange(
                      scopedIds.includes(d.id)
                        ? scopedIds.filter((x) => x !== d.id)
                        : [...scopedIds, d.id],
                    )
                  }
                >
                  <Check size={14} className={scopedIds.includes(d.id) ? "text-brand-600" : "opacity-0"} />
                  <span className="max-w-64 truncate">{d.title}</span>
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>

          <textarea
            ref={ref}
            rows={1}
            value={value}
            onChange={(e) => onValueChange(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Ask about your documents…"
            aria-label="Message"
            className="max-h-[200px] min-h-[24px] flex-1 resize-none bg-transparent py-1 text-body
                       leading-relaxed text-ink outline-none placeholder:text-ink-subtle"
          />

          {streaming ? (
            <Button variant="secondary" size="icon" onClick={onStop} aria-label="Stop generating" className="shrink-0">
              <Square size={13} className="fill-current" />
            </Button>
          ) : (
            <Button
              variant="primary"
              size="icon"
              onClick={submit}
              disabled={!value.trim()}
              aria-label="Send message"
              className="shrink-0"
            >
              <ArrowUp size={17} />
            </Button>
          )}
        </div>

        <p className="mt-1.5 px-1 text-micro text-ink-subtle">
          <kbd className="font-mono">Enter</kbd> to send ·{" "}
          <kbd className="font-mono">Shift</kbd>+<kbd className="font-mono">Enter</kbd> for a new line
          · Answers are grounded in scoped documents only
        </p>
      </div>
    </div>
  );
}
