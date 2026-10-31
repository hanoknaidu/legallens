import { useState } from "react";
import { Check, Copy, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";

export function MessageActions({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(null);

  return (
    <div className="mt-3 flex items-center gap-0.5 opacity-0 transition-opacity duration-[120ms]
                    focus-within:opacity-100 group-hover/msg:opacity-100">
      <Tooltip content={copied ? "Copied" : "Copy"}>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Copy answer"
          onClick={() => {
            navigator.clipboard?.writeText(content);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
          }}
        >
          {copied ? <Check size={14} className="text-grounded-fg" /> : <Copy size={14} />}
        </Button>
      </Tooltip>
      <Tooltip content="Regenerate">
        <Button variant="ghost" size="icon-sm" aria-label="Regenerate answer">
          <RefreshCw size={14} />
        </Button>
      </Tooltip>
      <Tooltip content="Good answer">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Mark as good answer"
          aria-pressed={vote === "up"}
          onClick={() => setVote(vote === "up" ? null : "up")}
        >
          <ThumbsUp size={14} className={vote === "up" ? "text-grounded-fg" : undefined} />
        </Button>
      </Tooltip>
      <Tooltip content="Needs work">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Mark as needing work"
          aria-pressed={vote === "down"}
          onClick={() => setVote(vote === "down" ? null : "down")}
        >
          <ThumbsDown size={14} className={vote === "down" ? "text-risk-fg" : undefined} />
        </Button>
      </Tooltip>
    </div>
  );
}
