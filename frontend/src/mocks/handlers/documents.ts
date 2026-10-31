import { http, HttpResponse, delay } from "msw";
import { documents } from "../fixtures/documents";
import { documentPage } from "../fixtures/pages";
import type { LegalDocument } from "@/types/domain";

const store: LegalDocument[] = [...documents];

export const documentHandlers = [
  http.get("/api/documents", async ({ request }) => {
    await delay(220);
    const matterId = new URL(request.url).searchParams.get("matterId");
    return HttpResponse.json(matterId ? store.filter((d) => d.matterId === matterId) : store);
  }),

  http.get("/api/documents/:id", async ({ params }) => {
    await delay(140);
    const doc = store.find((d) => d.id === params.id);
    if (!doc) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(doc);
  }),

  /**
   * One page of the text layer — what the viewer renders and highlights against.
   * The real backend serves this plus the original file for pdf.js; the shape here is
   * the shape it will return.
   */
  http.get("/api/documents/:id/page/:n", async ({ params }) => {
    await delay(120);
    const doc = store.find((d) => d.id === params.id);
    if (!doc) return new HttpResponse(null, { status: 404 });
    // A document that never produced a text layer has no page to serve. Saying so is
    // the point of FR-2 — the viewer must not render an empty page as though it were
    // a document that happens to be blank.
    if (doc.status !== "ready") return new HttpResponse(null, { status: 409 });

    const page = documentPage(doc.id, Number(params.n));
    if (!page) return new HttpResponse(null, { status: 416 });
    return HttpResponse.json(page);
  }),

  http.get("/api/documents/:id/status", async ({ params }) => {
    const doc = store.find((d) => d.id === params.id);
    if (!doc) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json({ id: doc.id, status: doc.status, failureReason: doc.failureReason });
  }),

  /**
   * Multipart, matching the real backend: the client sends the file itself, so the
   * mock reads a FormData body rather than JSON. The bytes are discarded here — what
   * this handler exists to reproduce is the queued → ready transition and the FR-2
   * rejection, neither of which needs the content.
   */
  http.post("/api/documents", async ({ request }) => {
    await delay(900);
    const form = await request.formData();
    const file = form.get("file") as File | null;
    const filename = file?.name ?? "upload";
    const sizeBytes = file?.size ?? 0;
    const matterId = (form.get("matterId") as string | null) ?? "m-001";
    const isScan = /scan|fax|copy/i.test(filename);
    const doc: LegalDocument = {
      id: `d-${Math.random().toString(36).slice(2, 8)}`,
      matterId,
      title: filename.replace(/\.[^.]+$/, "").replace(/[-_]/g, " "),
      filename,
      kind: "other",
      pages: 12,
      sizeBytes,
      status: isScan ? "failed" : "processing",
      failureReason: isScan
        ? "This PDF has no extractable text layer — it appears to be a scan. Optical character recognition is not available in this release, so the document cannot be indexed or cited."
        : undefined,
      uploadedAt: new Date().toISOString(),
    };
    store.unshift(doc);
    if (!isScan) {
      setTimeout(() => {
        const d = store.find((x) => x.id === doc.id);
        if (d) d.status = "ready";
      }, 6000);
    }
    return HttpResponse.json(doc, { status: 201 });
  }),

  http.delete("/api/documents/:id", async ({ params }) => {
    await delay(200);
    const i = store.findIndex((d) => d.id === params.id);
    if (i >= 0) store.splice(i, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
