import { Fragment, useState } from "react";
import { AlertTriangle, Copy, Download, Loader2 } from "lucide-react";
import { ApiError, exportMessage, saveBlob } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/Menu";
import { Markdown } from "@/components/chat/Markdown";
import type { Citation, DraftPayload } from "@/types/domain";

const PLACEHOLDER = /\[ATTORNEY INPUT REQUIRED[^\]]*\]/g;

/** Splits the body so placeholders can be rendered as visible, countable marks. */
function markPlaceholders(body: string) {
  const segments: Array<{ text: string; placeholder: boolean }> = [];
  let last = 0;
  for (const m of body.matchAll(PLACEHOLDER)) {
    if (m.index! > last) segments.push({ text: body.slice(last, m.index), placeholder: false });
    segments.push({ text: m[0], placeholder: true });
    last = m.index! + m[0].length;
  }
  if (last < body.length) segments.push({ text: body.slice(last), placeholder: false });
  return segments;
}

export function DraftDocument({
  draft,
  citations,
  messageId,
}: {
  draft: DraftPayload;
  citations: Citation[];
  /** Absent while the turn is still streaming, which is when export is not yet possible. */
  messageId?: string;
}) {
  const segments = markPlaceholders(draft.body);
  const gaps = segments.filter((s) => s.placeholder).length;
  const [exporting, setExporting] = useState<"docx" | "markdown" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(format: "docx" | "markdown") {
    if (!messageId) return;
    setExporting(format);
    setError(null);
    try {
      const { blob, filename } = await exportMessage(messageId, format);
      saveBlob(blob, filename);
    } catch (err) {
      // The server's `detail` is written to be read by a person, so prefer it.
      setError(err instanceof ApiError ? err.detail ?? err.message : "Export failed.");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-start gap-2.5 border-b border-attention-br bg-attention-bg px-4 py-2.5">
        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-attention-fg" />
        <div className="min-w-0 flex-1">
          <p className="text-caption font-semibold text-attention-fg">
            Unreviewed draft — requires attorney review
          </p>
          <p className="mt-0.5 text-caption text-attention-fg/85">
            {gaps} {gaps === 1 ? "section needs" : "sections need"} attorney input before this can be used.
          </p>
        </div>
      </div>

      <article className="px-6 py-5">
        <h3 className="mb-4 font-serif text-title font-semibold leading-snug text-ink">
          {draft.title}
        </h3>
        <div className="font-serif">
          {segments.map((seg, i) =>
            seg.placeholder ? (
              <mark
                key={i}
                className="my-1.5 mr-0.5 inline-block rounded-xs border border-attention-br bg-attention-bg
                           px-1.5 py-1 font-sans text-caption font-medium leading-snug text-attention-fg"
              >
                {seg.text}
              </mark>
            ) : (
              <Fragment key={i}>
                <Markdown content={seg.text} citations={citations} />
              </Fragment>
            ),
          )}
        </div>
      </article>

      <div className="flex items-center justify-end gap-2 border-t border-line bg-sunken px-4 py-2.5">
        {error && (
          <p role="alert" className="mr-auto text-caption text-risk-fg">
            {error}
          </p>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigator.clipboard?.writeText(draft.body)}
        >
          <Copy size={13} /> Copy
        </Button>
        <Menu>
          <MenuTrigger asChild>
            <Button variant="secondary" size="sm" disabled={!messageId || exporting !== null}>
              {exporting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              Export
            </Button>
          </MenuTrigger>
          <MenuContent>
            {/* No per-item disabled state needed: the trigger is disabled while the
                turn is streaming, so the menu cannot be opened in that state. */}
            <MenuItem onSelect={() => void download("docx")}>Word document (.docx)</MenuItem>
            <MenuItem onSelect={() => void download("markdown")}>Markdown (.md)</MenuItem>
          </MenuContent>
        </Menu>
      </div>
    </div>
  );
}
