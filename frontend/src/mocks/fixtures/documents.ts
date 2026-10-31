import type { LegalDocument } from "@/types/domain";

export const documents: LegalDocument[] = [
  {
    id: "d-msa",
    matterId: "m-001",
    title: "Master Services Agreement — Northwind / Arbor Systems",
    filename: "northwind-arbor-msa-executed.pdf",
    kind: "msa",
    pages: 42,
    sizeBytes: 3_412_880,
    status: "ready",
    uploadedAt: "2026-09-28T14:12:00.000Z",
  },
  {
    id: "d-nda",
    matterId: "m-001",
    title: "Mutual Non-Disclosure Agreement — Northwind / Arbor Systems",
    filename: "northwind-arbor-mutual-nda.pdf",
    kind: "nda",
    pages: 8,
    sizeBytes: 486_220,
    status: "ready",
    uploadedAt: "2026-09-28T14:09:00.000Z",
  },
  {
    id: "d-complaint",
    matterId: "m-002",
    title: "Complaint — Calder Industrial Group v. Brightpoint Manufacturing",
    filename: "calder-v-brightpoint-complaint.pdf",
    kind: "complaint",
    pages: 15,
    sizeBytes: 1_104_932,
    status: "ready",
    uploadedAt: "2026-10-01T09:40:00.000Z",
  },
  {
    id: "d-amend",
    matterId: "m-001",
    title: "Amendment No. 2 to Master Services Agreement",
    filename: "northwind-arbor-msa-amendment-2.pdf",
    kind: "other",
    pages: 6,
    sizeBytes: 302_118,
    status: "processing",
    uploadedAt: "2026-10-05T08:55:00.000Z",
  },
  {
    id: "d-scan",
    matterId: "m-002",
    title: "Brightpoint — Scanned Correspondence Bundle",
    filename: "brightpoint-correspondence-scan.pdf",
    kind: "other",
    pages: 23,
    sizeBytes: 8_940_117,
    status: "failed",
    failureReason:
      "This PDF has no extractable text layer — it appears to be a scan. Optical character recognition is not available in this release, so the document cannot be indexed or cited.",
    uploadedAt: "2026-10-05T08:51:00.000Z",
  },
];

export const documentById = (id: string) => documents.find((d) => d.id === id);
