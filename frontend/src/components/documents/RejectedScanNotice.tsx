import { ScanLine } from "lucide-react";

/**
 * FR-2 made visible.
 *
 * A scanned PDF is rejected rather than ingested as empty text, because a document that
 * silently indexes as nothing produces confident answers about nothing. The notice has to
 * do three things: say what happened, say why it is not a bug, and give the user a next
 * step they can actually take today.
 */
export function RejectedScanNotice({
  reason,
  compact = false,
}: {
  reason?: string;
  compact?: boolean;
}) {
  return (
    <div
      className="rounded-lg border border-risk-br bg-risk-bg px-4 py-3.5"
      role="status"
    >
      <div className="flex items-start gap-2.5">
        <ScanLine size={16} className="mt-0.5 shrink-0 text-risk-fg" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-ui font-medium text-risk-fg">No text layer — not indexed</p>
          <p className="mt-1 text-caption leading-relaxed text-risk-fg/90">
            {reason ??
              "This PDF has no extractable text layer — it appears to be a scan. Optical character recognition is not available in this release, so the document cannot be indexed or cited."}
          </p>
          {!compact && (
            <ul className="mt-2.5 space-y-1 text-caption leading-relaxed text-risk-fg/90">
              <li>• Upload a text-based PDF of the same document, if one exists.</li>
              <li>• Or run the scan through OCR yourself and upload the result.</li>
              <li>
                • The file is kept as uploaded — nothing was indexed, so nothing can be cited from
                it.
              </li>
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
