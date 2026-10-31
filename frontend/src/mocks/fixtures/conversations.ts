import type { Conversation } from "@/types/domain";

const h = (n: number) => new Date(Date.now() - n * 3_600_000).toISOString();

export const conversations: Conversation[] = [
  { id: "c-term",    title: "Early termination rights under the MSA", matterId: "m-001", createdAt: h(2),   updatedAt: h(2),   lastIntent: "ask" },
  { id: "c-summary", title: "Summary — Northwind / Arbor MSA",        matterId: "m-001", createdAt: h(6),   updatedAt: h(5),   lastIntent: "summary" },
  { id: "c-draft",   title: "Memo — enforceability of the non-compete", matterId: "m-001", createdAt: h(27), updatedAt: h(26), lastIntent: "draft" },
  { id: "c-indem",   title: "Indemnification caps and carve-outs",     matterId: "m-001", createdAt: h(31), updatedAt: h(30), lastIntent: "ask" },
  { id: "c-insure",  title: "Insurance requirements — not addressed",  matterId: "m-001", createdAt: h(96), updatedAt: h(96), lastIntent: "ask" },
  { id: "c-venue",   title: "Venue and governing law across agreements", matterId: "m-002", createdAt: h(220), updatedAt: h(219), lastIntent: "ask" },
];
