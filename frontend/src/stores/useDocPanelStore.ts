import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { layout } from "@/lib/tokens";
import type { Citation } from "@/types/domain";

/**
 * The document panel's state, held globally rather than passed down.
 *
 * A citation chip can be nested four levels inside a markdown table cell inside a draft
 * inside a message. Threading an `onOpen` callback down that path would put the panel in
 * the props of everything between, so the chip calls this store directly instead. The
 * chat page then only has to keep the URL in step (FR-8's shareable `?doc=&page=`).
 */
interface DocPanelState {
  open: boolean;
  documentId: string | null;
  page: number;
  /** The citation that opened the panel. Its `citedText` is the span to highlight. */
  active: Citation | null;
  /** The control that opened the panel, so focus can return to it on close (NFR-12). */
  trigger: HTMLElement | null;
  /** Panel width in px when docked. Persisted — a resize is a preference, not a session. */
  width: number;
  zoom: number;

  openCitation: (citation: Citation, trigger?: HTMLElement | null) => void;
  openDocument: (documentId: string, page?: number, trigger?: HTMLElement | null) => void;
  close: () => void;
  setPage: (page: number) => void;
  setWidth: (width: number) => void;
  setZoom: (zoom: number) => void;
}

export const ZOOM_STEPS = [0.9, 1, 1.15, 1.3, 1.5] as const;

const clampWidth = (w: number) =>
  Math.min(layout.panelMaxPx, Math.max(layout.panelMinPx, Math.round(w)));

export const useDocPanelStore = create<DocPanelState>()(
  persist(
    (set) => ({
      open: false,
      documentId: null,
      page: 1,
      active: null,
      trigger: null,
      width: layout.panelDefaultPx,
      zoom: 1,

      openCitation: (citation, trigger = null) =>
        set({
          open: true,
          documentId: citation.documentId,
          page: citation.page,
          active: citation,
          trigger,
        }),

      openDocument: (documentId, page = 1, trigger = null) =>
        set((s) => ({
          open: true,
          documentId,
          page,
          // Keep the highlight only while we stay inside the document it belongs to.
          active: s.documentId === documentId ? s.active : null,
          trigger,
        })),

      close: () => set({ open: false, active: null, trigger: null }),
      setPage: (page) => set({ page: Math.max(1, page) }),
      setWidth: (width) => set({ width: clampWidth(width) }),
      setZoom: (zoom) => set({ zoom }),
    }),
    {
      name: "legal-lens-doc-panel",
      storage: createJSONStorage(() => localStorage),
      // Which document was open is a property of the URL, not of the browser. Only the
      // chrome preferences survive a reload.
      partialize: (s) => ({ width: s.width, zoom: s.zoom }),
    },
  ),
);
