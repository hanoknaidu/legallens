import { cn } from "@/lib/cn";

/** Type-only branding with a small lens-over-document mark. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true" className="shrink-0">
        <rect x="3" y="2.5" width="12" height="16" rx="2" className="fill-brand-100" />
        <path d="M6 6.5h6M6 9.5h6M6 12.5h3.5" stroke="currentColor" strokeWidth="1.3"
              strokeLinecap="round" className="text-brand-700" />
        <circle cx="14.5" cy="13.5" r="4.6" className="fill-surface" />
        <circle cx="14.5" cy="13.5" r="4.1" fill="none" stroke="currentColor"
                strokeWidth="1.6" className="text-brand-600" />
        <path d="M17.8 16.8 20 19" stroke="currentColor" strokeWidth="1.8"
              strokeLinecap="round" className="text-brand-600" />
      </svg>
      <span className="text-[0.9375rem] font-semibold tracking-[-0.011em] text-ink">
        Legal Lens
      </span>
    </span>
  );
}
