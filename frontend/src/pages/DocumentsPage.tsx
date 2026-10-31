import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Trash2, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatBytes, formatDate } from "@/lib/format";
import { MAX_UPLOAD_BYTES, MAX_UPLOAD_PAGES } from "@/lib/constants";
import { useUIStore } from "@/stores/useUIStore";
import { useDocPanelStore } from "@/stores/useDocPanelStore";
import { TopBar } from "@/components/shell/TopBar";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { IngestStatus } from "@/components/documents/IngestStatus";
import { RejectedScanNotice } from "@/components/documents/RejectedScanNotice";
import type { LegalDocument } from "@/types/domain";

const ACCEPTED = /\.(pdf|docx|txt)$/i;

interface Rejection {
  filename: string;
  reason: string;
}

function DocumentCard({
  doc,
  onDelete,
}: {
  doc: LegalDocument;
  onDelete: (doc: LegalDocument) => void;
}) {
  const openDocument = useDocPanelStore((s) => s.openDocument);

  return (
    <li className="rounded-lg border border-line bg-surface p-4 transition-colors duration-[120ms]
                   hover:border-line-strong">
      <div className="flex items-start gap-3">
        <FileText size={18} className="mt-0.5 shrink-0 text-ink-subtle" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-ui font-medium text-ink">
            <Link
              to={`/documents/${doc.id}`}
              className="rounded-xs hover:text-brand-700 hover:underline underline-offset-2"
            >
              {doc.title}
            </Link>
          </h3>
          <p className="mt-0.5 truncate font-mono text-micro text-ink-subtle">{doc.filename}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-muted">
            <IngestStatus status={doc.status} />
            <span>{doc.pages} pages</span>
            <span>{formatBytes(doc.sizeBytes)}</span>
            <span>{formatDate(doc.uploadedAt)}</span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {doc.status === "ready" && (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => openDocument(doc.id, 1, e.currentTarget)}
              aria-label={`Open ${doc.title} in the side panel`}
            >
              Open
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete ${doc.title}`}
            onClick={() => onDelete(doc)}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>

      {doc.status === "failed" && (
        <div className="mt-3">
          <RejectedScanNotice reason={doc.failureReason} compact />
        </div>
      )}
    </li>
  );
}

export function DocumentsPage() {
  const qc = useQueryClient();
  const activeMatterId = useUIStore((s) => s.activeMatterId);
  const [dragging, setDragging] = useState(false);
  const [rejections, setRejections] = useState<Rejection[]>([]);
  const [deleting, setDeleting] = useState<LegalDocument | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: documents, isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.documents.list(),
    // Poll only while something is actually in flight — a timer that runs forever is a
    // timer nobody notices is running.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((d) => d.status === "processing" || d.status === "queued")
        ? 2000
        : false,
  });

  const upload = useMutation({
    mutationFn: (file: File) => api.documents.upload(file, activeMatterId ?? "m-001"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.documents.remove(id),
    // Optimistic, with the row put back if the server refuses. A deleted document that
    // lingers until a refetch reads as a failed click, and the user clicks again.
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["documents"] });
      const previous = qc.getQueryData<LegalDocument[]>(["documents"]);
      qc.setQueryData<LegalDocument[]>(["documents"], (list) =>
        (list ?? []).filter((d) => d.id !== id),
      );
      return { previous };
    },
    onError: (_e, _id, context) => qc.setQueryData(["documents"], context?.previous),
    onSettled: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  const visible = (documents ?? []).filter((d) => !activeMatterId || d.matterId === activeMatterId);

  /** Client-side gate for the two limits the user can see before a round trip. */
  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    const refused: Rejection[] = [];
    for (const file of Array.from(files)) {
      if (!ACCEPTED.test(file.name)) {
        refused.push({ filename: file.name, reason: "Only PDF, DOCX and TXT files can be indexed." });
      } else if (file.size > MAX_UPLOAD_BYTES) {
        refused.push({
          filename: file.name,
          reason: `${formatBytes(file.size)} exceeds the ${formatBytes(MAX_UPLOAD_BYTES)} limit.`,
        });
      } else {
        upload.mutate(file);
      }
    }
    setRejections(refused);
  };

  return (
    <>
      <TopBar title="Documents" showPanelToggle />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-6">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              handleFiles(e.dataTransfer.files);
            }}
            className={cn(
              "rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors duration-[120ms]",
              dragging ? "border-brand-500 bg-brand-50" : "border-line bg-surface",
            )}
          >
            <Upload size={20} className="mx-auto mb-2 text-ink-subtle" aria-hidden="true" />
            <p className="text-ui font-medium text-ink">
              Drop documents here, or{" "}
              <button
                className="text-brand-600 underline underline-offset-2 hover:text-brand-700"
                onClick={() => inputRef.current?.click()}
              >
                browse
              </button>
            </p>
            <p className="mt-1 text-caption text-ink-muted">
              PDF, DOCX or TXT · up to {formatBytes(MAX_UPLOAD_BYTES)} and {MAX_UPLOAD_PAGES} pages ·
              scanned PDFs are not supported in this release
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.txt"
              className="sr-only"
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>

          {upload.isPending && (
            <div className="mt-3 rounded-md border border-line bg-surface px-3 py-2.5">
              <div className="flex items-center justify-between text-caption text-ink-muted">
                <span>Uploading…</span>
                <span className="font-mono text-micro">validating and queueing</span>
              </div>
              {/* Indeterminate: the mock transport reports no progress, and a fake
                  percentage that jumps to 100% is worse than an honest bar. */}
              <div
                className="mt-2 h-1 overflow-hidden rounded-pill bg-sunken"
                role="progressbar"
                aria-label="Upload progress"
              >
                <div className="h-full w-1/3 rounded-pill bg-brand-500 animate-indeterminate" />
              </div>
            </div>
          )}

          {rejections.length > 0 && (
            <ul className="mt-3 space-y-2">
              {rejections.map((r) => (
                <li
                  key={r.filename}
                  className="rounded-md border border-risk-br bg-risk-bg px-3 py-2 text-caption text-risk-fg"
                >
                  <span className="font-medium">{r.filename}</span> — {r.reason}
                </li>
              ))}
            </ul>
          )}

          <h2 className="mb-3 mt-7 text-ui font-semibold text-ink">
            {visible.length} {visible.length === 1 ? "document" : "documents"}
          </h2>

          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-lg" />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <p className="rounded-lg border border-line bg-surface px-4 py-8 text-center text-ui text-ink-muted">
              No documents in this matter yet.
            </p>
          ) : (
            <ul className="space-y-3">
              {visible.map((d) => (
                <DocumentCard key={d.id} doc={d} onDelete={setDeleting} />
              ))}
            </ul>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.title ?? ""}”?`}
        description="The document and everything derived from it will be removed."
        consequence="This purges the original file, its extracted text, its chunks and its embeddings. Answers that cited it keep their text but lose their source."
        onConfirm={() => deleting && remove.mutate(deleting.id)}
        pending={remove.isPending}
      />
    </>
  );
}
