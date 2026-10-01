import { z } from "zod";
import { ApiError, apiGet, apiPath } from "./client";

// A document's revision history (design/artifact §32). Each schema mirrors its builder in
// nora/web/document_history.py. As in ./documents.ts, an old revision's `document` stays
// `unknown`: the renderer validates it before drawing a line.

/** Who wrote a revision. Nora records the tag she wrote it at, not a person, so a
 * revision of hers is `nora`; a restore from the portal records the person. */
export const RevisionWriterSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("nora") }),
  z.object({ kind: z.literal("person"), personId: z.string(), name: z.string() }),
]);
export type RevisionWriter = z.infer<typeof RevisionWriterSchema>;

export const RevisionSummarySchema = z.object({
  revision: z.number().int().min(1),
  title: z.string(),
  sha256: z.string(),
  recordedAt: z.string(),
  writtenBy: RevisionWriterSchema,
});
export type RevisionSummary = z.infer<typeof RevisionSummarySchema>;

export const DocumentRevisionsSchema = z.object({
  id: z.string(),
  title: z.string(),
  /** The current revision's number. */
  revision: z.number().int().min(1),
  state: z.enum(["active", "archived"]),
  /** Newest first. */
  revisions: z.array(RevisionSummarySchema),
});
export type DocumentRevisions = z.infer<typeof DocumentRevisionsSchema>;

export const DocumentRevisionSchema = RevisionSummarySchema.extend({
  id: z.string(),
  currentRevision: z.number().int().min(1),
  state: z.enum(["active", "archived"]),
  document: z.unknown(),
  media: z.array(z.string()),
});
export type DocumentRevision = z.infer<typeof DocumentRevisionSchema>;

export const fetchRevisions = (id: string, signal?: AbortSignal) =>
  apiGet(`/documents/${encodeURIComponent(id)}/revisions`, DocumentRevisionsSchema, { signal });

export const fetchRevision = (id: string, n: number, signal?: AbortSignal) =>
  apiGet(`/documents/${encodeURIComponent(id)}/revisions/${n}`, DocumentRevisionSchema, { signal });

/** Why a restore did not happen, when the document itself is fine: someone revised it
 * since (`conflict`), it is archived (`not_active`), or this build cannot draw the old
 * spec (`invalid`). */
export type RestoreRefusal = "conflict" | "not_active" | "invalid";

export class RestoreRefusedError extends Error {
  constructor(readonly reason: RestoreRefusal) {
    super(`restore refused: ${reason}`);
    this.name = "RestoreRefusedError";
  }
}

/** Publish revision `n` again, as a new revision on top of `baseRevision` (the current
 * revision the viewer saw). Resolves to the new revision's number once Nora has stored
 * it; every failure rejects, so the page never says "restored" for a write that did not
 * happen. */
export async function restoreRevision(id: string, n: number, baseRevision: number): Promise<number> {
  const path = `/documents/${encodeURIComponent(id)}/revisions/${n}/restore`;
  let res: Response;
  try {
    res = await fetch(apiPath(path), {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ baseRevision }),
    });
  } catch {
    throw new ApiError(`network: ${path}`, 0, "unavailable");
  }
  if (res.status === 401) throw new ApiError(`signed out: ${path}`, 401, "signed-out");
  if (res.status === 404) throw new ApiError(`not found: ${path}`, 404, "not-found");
  if (res.status === 409) {
    const body = (await res.json().catch(() => null)) as { status?: string } | null;
    const reason = body?.status;
    if (reason === "conflict" || reason === "not_active" || reason === "invalid") {
      throw new RestoreRefusedError(reason);
    }
  }
  if (!res.ok) throw new ApiError(`upstream ${res.status}: ${path}`, res.status, "unavailable");
  const body = (await res.json().catch(() => null)) as { revision?: unknown } | null;
  if (typeof body?.revision !== "number") throw new ApiError(`bad payload: ${path}`, 200, "bad-payload");
  return body.revision;
}
