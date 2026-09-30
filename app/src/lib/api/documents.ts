import { z } from "zod";
import { apiGet } from "./client";

// Documents (ADR 0020). Each schema mirrors its builder in nora/web/documents.py.
// The spec itself is NOT parsed here: `document` stays `unknown` and the renderer
// validates it again before drawing a line (src/lib/documents/catalog.ts parseDocument),
// so a stored spec this build does not understand is shown as such, never half-drawn.

export const DocumentSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  revision: z.number().int().min(1),
  // The room's platform only ("signal"): never its chat id.
  platform: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  createdBy: z.object({ personId: z.string(), name: z.string() }),
});
export type DocumentSummary = z.infer<typeof DocumentSummarySchema>;

export const DocumentsPageSchema = z.object({
  documents: z.array(DocumentSummarySchema),
  page: z.number().int(),
  pageSize: z.number().int(),
  total: z.number().int(),
});
export type DocumentsPage = z.infer<typeof DocumentsPageSchema>;

export const DocumentDetailSchema = DocumentSummarySchema.extend({
  document: z.unknown(),
  media: z.array(z.string()),
});
export type DocumentDetail = z.infer<typeof DocumentDetailSchema>;

export const fetchDocuments = (page: number, signal?: AbortSignal) =>
  apiGet("/documents", DocumentsPageSchema, { query: { page }, signal });
export const fetchDocument = (id: string, signal?: AbortSignal) =>
  apiGet(`/documents/${encodeURIComponent(id)}`, DocumentDetailSchema, { signal });
