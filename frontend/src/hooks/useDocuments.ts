import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { LegalDocument } from "@/types/domain";

/**
 * The document library, shared by every component that needs to name a document.
 *
 * Components used to reach into the MSW fixtures for this, which worked exactly until the
 * backend existed. One query key, one cache, and the only thing that changes when the real
 * API arrives is where the data came from.
 */
export function useDocuments(matterId?: string) {
  return useQuery({
    queryKey: matterId ? ["documents", matterId] : ["documents"],
    queryFn: () => api.documents.list(matterId),
    // Ingest status moves on its own, but not so fast that every mount needs a refetch.
    staleTime: 10_000,
  });
}

/** `(id) => document | undefined`, for rendering scope chips and citation targets. */
export function useDocumentLookup() {
  const { data } = useDocuments();
  const byId = new Map((data ?? []).map((d: LegalDocument) => [d.id, d]));
  return (id: string) => byId.get(id);
}
