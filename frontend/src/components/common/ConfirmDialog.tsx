import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";

/**
 * One confirm dialog, used everywhere something is destroyed.
 *
 * `consequence` exists because "Are you sure?" is not information. Deleting a document
 * also discards its extracted text, its chunks and its embeddings, and a user who is told
 * only "this cannot be undone" has to guess what "this" covers.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  consequence,
  confirmLabel = "Delete",
  onConfirm,
  pending = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  consequence?: React.ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  pending?: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}>
      {consequence && (
        <div className="mb-4 rounded-md border border-line bg-sunken px-3 py-2.5 text-caption
                        leading-relaxed text-ink-muted">
          {consequence}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
          Cancel
        </Button>
        <Button
          variant="danger"
          autoFocus
          disabled={pending}
          onClick={() => {
            onConfirm();
            onOpenChange(false);
          }}
        >
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}
