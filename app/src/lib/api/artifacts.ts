import { z } from "zod";
import { apiGet } from "./client";

// Artifacts (ADR 0023): the files Nora sent out of her computer in the rooms that name
// the viewer. Each schema mirrors its builder in nora/web/artifacts.py.

export const ArtifactSchema = z.object({
  // A link's 12-hex artifact_id, or an attachment's UUID: the one address the portal uses.
  id: z.string(),
  // `link`: a link Nora sent (its newest revision); `chat`: a file attached in the chat.
  kind: z.enum(["link", "chat"]),
  title: z.string(),
  mediaType: z.string(),
  size: z.number().int().min(0),
  // The room's platform ("signal"), never its chat id.
  platform: z.string(),
  // The operator's label of the room, only when the viewer is in it; "" otherwise.
  roomLabel: z.string().default(""),
  createdAt: z.string(),
  revised: z.boolean().default(false),
});
export type Artifact = z.infer<typeof ArtifactSchema>;

export const ArtifactsPageSchema = z.object({
  artifacts: z.array(ArtifactSchema),
  page: z.number().int(),
  pageSize: z.number().int(),
  total: z.number().int(),
  query: z.string().default(""),
});
export type ArtifactsPage = z.infer<typeof ArtifactsPageSchema>;

export const fetchArtifacts = (page: number, search = "", signal?: AbortSignal) =>
  apiGet("/artifacts", ArtifactsPageSchema, {
    query: { page, q: search || undefined },
    signal,
  });

export const fetchArtifact = (id: string, signal?: AbortSignal) =>
  apiGet(`/artifacts/${encodeURIComponent(id)}`, ArtifactSchema, { signal });

/** Where the browser fetches the file itself (src/app/api/v1/artifacts/[id]/download). */
export const artifactDownloadPath = (id: string) => `/api/v1/artifacts/${encodeURIComponent(id)}/download`;
