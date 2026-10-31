import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useDocPanelStore, ZOOM_STEPS } from "@/stores/useDocPanelStore";
import { TopBar } from "@/components/shell/TopBar";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { IngestStatus } from "@/components/documents/IngestStatus";
import { RejectedScanNotice } from "@/components/documents/RejectedScanNotice";
import { PageSurface } from "@/components/documents/PageSurface";
import { PageToolbar } from "@/components/documents/PageToolbar";

/**
 * The full-width document viewer.
 *
 * Same surface and same toolbar as the side panel — one renderer, two framings, so a
 * document cannot look like two different documents depending on where it is opened. The
 * page lives in the URL here too, because a page reference is the thing lawyers quote.
 */
export function DocumentPage() {
  const { documentId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [confirming, setConfirming] = useState(false);
  const closePanel = useDocPanelStore((s) => s.close);
  const [zoom, setZoom] = useState<number>(ZOOM_STEPS[1]);

  const page = Math.max(1, Number(params.get("page")) || 1);
  const setPage = (n: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(n));
    setParams(next, { replace: true });
  };

  // The side panel would be showing the same document behind this one.
  useEffect(() => closePanel(), [closePanel, documentId]);

  const { data: doc, isLoading } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.documents.get(documentId!),
    enabled: !!documentId,
  });

  const ready = doc?.status === "ready";
  const { data: pageData, isLoading: pageLoading } = useQuery({
    queryKey: ["document-page", documentId, page],
    queryFn: () => api.documents.page(documentId!, page),
    enabled: !!documentId && ready,
    staleTime: Infinity,
  });

  const remove = useMutation({
    mutationFn: () => api.documents.remove(documentId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      navigate("/documents");
    },
  });

  return (
    <>
      <TopBar
        title={doc?.title ?? "Document"}
        meta={
          <div className="flex items-center gap-2">
            {doc && <IngestStatus status={doc.status} />}
            <Button variant="ghost" size="sm" onClick={() => setConfirming(true)} disabled={!doc}>
              <Trash2 size={14} /> Delete
            </Button>
          </div>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-line bg-surface px-3 py-2">
          <Link
            to="/documents"
            className="inline-flex items-center gap-1.5 text-caption font-medium text-ink-muted
                       hover:text-ink"
          >
            <ArrowLeft size={14} /> All documents
          </Link>
          {doc && (
            <p className="truncate font-mono text-micro text-ink-subtle">
              {doc.filename} · {doc.pages} pages · {formatBytes(doc.sizeBytes)} ·{" "}
              {formatDate(doc.uploadedAt)}
            </p>
          )}
        </div>

        {ready && doc && (
          <PageToolbar
            page={page}
            totalPages={doc.pages}
            zoom={zoom}
            onPageChange={setPage}
            onZoomChange={setZoom}
          />
        )}

        <div className="min-h-0 flex-1 overflow-y-auto bg-sunken/60">
          {isLoading ? (
            <div className="px-4 py-6">
              <div className="mx-auto max-w-[46em] space-y-3 rounded-md border border-line bg-surface p-10">
                {Array.from({ length: 14 }).map((_, i) => (
                  <Skeleton key={i} className="h-3.5 w-full" />
                ))}
              </div>
            </div>
          ) : !doc ? (
            <p className="px-4 py-10 text-center text-ui text-ink-muted">
              This document no longer exists.
            </p>
          ) : doc.status === "failed" ? (
            <div className="mx-auto max-w-2xl p-6">
              <RejectedScanNotice reason={doc.failureReason} />
            </div>
          ) : !ready ? (
            <div className="mx-auto max-w-2xl p-6">
              <div className="rounded-lg border border-line bg-surface px-4 py-8 text-center">
                <IngestStatus status={doc.status} size="md" />
                <p className="mt-3 text-ui text-ink-muted">
                  Indexing in progress. Pages become viewable and citable when it finishes.
                </p>
              </div>
            </div>
          ) : pageLoading || !pageData ? (
            <div className="px-4 py-6">
              <div className="mx-auto max-w-[46em] space-y-3 rounded-md border border-line bg-surface p-10">
                {Array.from({ length: 14 }).map((_, i) => (
                  <Skeleton key={i} className="h-3.5 w-full" />
                ))}
              </div>
            </div>
          ) : (
            <PageSurface page={pageData} totalPages={doc.pages} spans={[]} zoom={zoom} />
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Delete “${doc?.title ?? ""}”?`}
        description="The document and everything derived from it will be removed."
        consequence="This purges the original file, its extracted text, its chunks and its embeddings. Answers that cited it keep their text but lose their source."
        onConfirm={() => remove.mutate()}
        pending={remove.isPending}
      />
    </>
  );
}
