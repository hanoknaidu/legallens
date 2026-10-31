import { MessageSquareText, FileSearch, PenLine } from "lucide-react";
import { Wordmark } from "@/components/shell/Wordmark";

const SUGGESTIONS = [
  {
    intent: "Ask",
    icon: MessageSquareText,
    prompts: [
      "What does this contract say about early termination?",
      "Do the MSA and the NDA agree on governing law and venue?",
    ],
  },
  {
    intent: "Summarise",
    icon: FileSearch,
    prompts: ["Summarise the Northwind / Arbor MSA."],
  },
  {
    intent: "Draft",
    icon: PenLine,
    prompts: [
      "Draft a memo on whether the non-compete is enforceable against subcontractors.",
    ],
  },
];

export function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center px-6 py-10">
      <Wordmark className="mb-4 scale-110 origin-left" />
      <h2 className="text-balance text-title font-semibold tracking-[-0.015em] text-ink">
        Ask, summarise or draft — from your own documents.
      </h2>
      <p className="mt-1.5 max-w-lg text-ui text-ink-muted">
        Every answer is grounded in the documents you upload, and every claim links back to the
        page it came from. If the documents don’t answer a question, Legal Lens says so.
      </p>

      <div className="mt-7 space-y-5">
        {SUGGESTIONS.map(({ intent, icon: Icon, prompts }) => (
          <div key={intent}>
            <h3 className="mb-2 flex items-center gap-1.5 text-caption font-medium uppercase
                           tracking-wider text-ink-subtle">
              <Icon size={12} /> {intent}
            </h3>
            <div className="space-y-1.5">
              {prompts.map((p) => (
                <button
                  key={p}
                  onClick={() => onPick(p)}
                  className="block w-full rounded-md border border-line bg-surface px-3.5 py-2.5
                             text-left text-ui text-ink-muted transition-colors duration-[120ms]
                             hover:border-brand-500 hover:bg-brand-50 hover:text-brand-900"
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
