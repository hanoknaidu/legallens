import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocPanelStore } from "@/stores/useDocPanelStore";

/**
 * Keeps the document panel and the URL in step: `/chat/:id?doc=:docId&page=:n`.
 *
 * The point is a link someone can send: "look at what this answer is citing" has to
 * survive a paste into a message. The store stays the source of truth for the panel; this
 * hook only mirrors it, and remembers the last query string it wrote so a mirrored change
 * is not mistaken for a navigation and echoed back.
 */
export function useDocPanelUrl() {
  const [params, setParams] = useSearchParams();
  const { open, documentId, page, openDocument, close } = useDocPanelStore();
  const lastWritten = useRef<string | null>(null);

  // URL → store. Runs on first load and on real navigation (back button, pasted link).
  useEffect(() => {
    const search = params.toString();
    if (search === lastWritten.current) return;

    const doc = params.get("doc");
    const wanted = Math.max(1, Number(params.get("page")) || 1);
    if (doc) {
      if (doc !== documentId || wanted !== page || !open) openDocument(doc, wanted);
    } else if (open) {
      close();
    }
    // Deliberately keyed on the URL alone: reacting to store changes here is what turns
    // a mirror into a loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  // store → URL
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (open && documentId) {
      next.set("doc", documentId);
      next.set("page", String(page));
    } else {
      next.delete("doc");
      next.delete("page");
    }
    const search = next.toString();
    if (search === params.toString()) return;
    lastWritten.current = search;
    // replace, not push: paging through a document should not fill the back button with
    // every page the reader passed through on the way.
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, documentId, page]);
}
