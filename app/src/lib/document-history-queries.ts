"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as history from "./api/document-history";

// The history reads (design/artifact §32), beside src/lib/queries.ts rather than in it:
// keyed under "document" so everything that re-reads a document (a state change, a
// restore) re-reads its history too.
const M = 60 * 1000;

export const historyKeys = {
  revisions: (id: string) => ["document", id, "revisions"] as const,
  revision: (id: string, n: number) => ["document", id, "revision", n] as const,
};

export const useDocumentRevisions = (id: string) =>
  useQuery({
    queryKey: historyKeys.revisions(id),
    queryFn: ({ signal }) => history.fetchRevisions(id, signal),
    staleTime: 1 * M,
  });

export const useDocumentRevision = (id: string, n: number) =>
  useQuery({
    queryKey: historyKeys.revision(id, n),
    queryFn: ({ signal }) => history.fetchRevision(id, n, signal),
    // A revision never changes once written; only the current number moves.
    staleTime: 1 * M,
  });

/** Restore revision `n` as a new revision. On success every document read and every list
 * is read again, so what is shown is what Nora now holds. */
export const useRestoreRevision = (id: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ n, base }: { n: number; base: number }) => history.restoreRevision(id, n, base),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["documents"] }),
        client.invalidateQueries({ queryKey: ["document"] }),
      ]);
    },
  });
};
