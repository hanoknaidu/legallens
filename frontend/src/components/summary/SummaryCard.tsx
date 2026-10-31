import { CitationChip } from "@/components/citations/CitationChip";
import { Badge } from "@/components/ui/Badge";
import type { Citation, SummaryField } from "@/types/domain";

/**
 * The nine BRD fields. Absent clauses are stated explicitly rather than
 * omitted — a missing limitation-of-liability clause matters as much as
 * a present one, and silence would hide it.
 */
export function SummaryCard({
  fields,
  citations,
}: {
  fields: SummaryField[];
  citations: Citation[];
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line bg-sunken px-4 py-2.5">
        <h3 className="text-ui font-semibold text-ink">Structured summary</h3>
        <Badge tone="neutral">{fields.filter((f) => f.value).length} of {fields.length} populated</Badge>
      </div>
      <dl className="divide-y divide-line">
        {fields.map((f) => (
          <div key={f.key} className="grid gap-1 px-4 py-3 sm:grid-cols-[13rem_1fr] sm:gap-4">
            <dt className="pt-0.5 text-caption font-medium uppercase tracking-wide text-ink-subtle">
              {f.label}
            </dt>
            <dd className="min-w-0">
              {f.value ? (
                <p className="font-serif text-[0.9375rem] leading-relaxed text-ink">
                  {f.value}
                  {f.cites.map((n) => {
                    const c = citations.find((x) => x.n === n);
                    return c ? <CitationChip key={n} citation={c} /> : null;
                  })}
                </p>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-sm border border-attention-br
                                 bg-attention-bg px-2 py-1 text-caption text-attention-fg">
                  Not found in this document
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
