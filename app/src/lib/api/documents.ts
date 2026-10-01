import { z } from "zod";
import { ApiError, apiGet, apiPath } from "./client";

// Documents (ADR 0020). Each schema mirrors its builder in nora/web/documents.py.
// The spec itself is NOT parsed here: `document` stays `unknown` and the renderer
// validates it again before drawing a line (src/lib/documents/catalog.ts parseDocument),
// so a stored spec this build does not understand is shown as such, never half-drawn.

export const DocumentSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  revision: z.number().int().min(1),
  // The room's platform ("signal"), never its chat id.
  platform: z.string(),
  // The operator's label of the room, only when the viewer is in that room; "" otherwise
  // (and from a Nora that predates it), and the platform is shown instead.
  roomLabel: z.string().default(""),
  createdAt: z.string(),
  updatedAt: z.string(),
  createdBy: z.object({ personId: z.string(), name: z.string() }),
  // A deleted document is never sent; a Nora from before lifecycle sends no state at all.
  state: z.enum(["active", "archived"]).default("active"),
});
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;

export const DocumentsPageSchema = z.object({
  documents: z.array(DocumentSummarySchema),
  page: z.number().int(),
  pageSize: z.number().int(),
  total: z.number().int(),
  // The search the list answers, as Nora normalised it; "" for the whole list.
  query: z.string().default(""),
});
export type DocumentsPage = z.infer<typeof DocumentsPageSchema>;

export const DocumentDetailSchema = DocumentSummarySchema.extend({
  document: z.unknown(),
  media: z.array(z.string()),
});
export type DocumentDetail = z.infer<typeof DocumentDetailSchema>;

/** What the list shows: the active documents, or the Archived filter. */
export type DocumentFilter = "active" | "archived";
/** What a person can set: archive, delete (restorable by Nora only), or restore. */
export type DocumentStateChange = "active" | "archived" | "deleted";

export const fetchDocuments = (
  page: number,
  state: DocumentFilter = "active",
  search = "",
  signal?: AbortSignal,
) =>
  apiGet("/documents", DocumentsPageSchema, {
    query: { page, state: state === "active" ? undefined : state, q: search || undefined },
    signal,
  });

/** Archive, delete or restore one document. Resolves when Nora has recorded it; a 404
 * (gone, or not the viewer's) and every failure reject, so the UI never says "done"
 * for a change that did not happen. */
export async function setDocumentState(id: string, state: DocumentStateChange): Promise<void> {
  const path = `/documents/${encodeURIComponent(id)}/state`;
  let res: Response;
  try {
    res = await fetch(apiPath(path), {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ state }),
    });
  } catch {
    throw new ApiError(`network: ${path}`, 0, "unavailable");
  }
  if (res.status === 401) throw new ApiError(`signed out: ${path}`, 401, "signed-out");
  if (res.status === 404) throw new ApiError(`not found: ${path}`, 404, "not-found");
  if (!res.ok) throw new ApiError(`upstream ${res.status}: ${path}`, res.status, "unavailable");
}
export const fetchDocument = (id: string, signal?: AbortSignal) =>
  apiGet(`/documents/${encodeURIComponent(id)}`, DocumentDetailSchema, { signal });
