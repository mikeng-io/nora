import { z } from "zod";
import { ApiError, apiGet, apiPath } from "./client";

// The circle's downloads: media yt-dlp pulled for URLs someone submitted. These
// are portal-local routes (src/app/api/v1/downloads/*), not upstream reads —
// the schemas mirror src/server/downloads/store.ts's DownloadJob.

export const DownloadStatusSchema = z.enum(["queued", "probing", "downloading", "done", "failed"]);
export type DownloadStatus = z.infer<typeof DownloadStatusSchema>;

export const DownloadJobSchema = z.object({
  id: z.string(),
  url: z.string(),
  status: DownloadStatusSchema,
  progress: z.object({
    percent: z.number().nullable(),
    totalBytes: z.number().nullable(),
    speedBps: z.number().nullable(),
  }),
  title: z.string().nullable(),
  extractor: z.string().nullable(),
  fileName: z.string().nullable(),
  fileSize: z.number().nullable(),
  mediaType: z.string().nullable(),
  error: z.string().nullable(),
  submittedBy: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  files: z.array(z.string()).optional(),
  hasThumb: z.boolean().optional(),
  converting: z.boolean().optional(),
  convertTargets: z.array(z.string()).optional(),
});
export type DownloadJob = z.infer<typeof DownloadJobSchema>;

export const DownloadsListSchema = z.object({
  jobs: z.array(DownloadJobSchema),
  usedBytes: z.number(),
});

export const SubmitResultSchema = z.object({
  jobs: z.array(DownloadJobSchema),
  rejected: z.array(
    z.object({ url: z.string(), error: z.string(), existingId: z.string().optional() }),
  ),
});
export type SubmitResult = z.infer<typeof SubmitResultSchema>;

export const fetchDownloads = (signal?: AbortSignal) =>
  apiGet("/downloads", DownloadsListSchema, { signal });

export const downloadContentPath = (id: string, file?: string) =>
  `/api/v1/downloads/${encodeURIComponent(id)}/content${file ? `?file=${encodeURIComponent(file)}` : ""}`;

/** Submit a batch of URLs. A job that failed its validation or already exists is
 * reported in `rejected`, never silently dropped. */
export async function submitDownloads(urls: string[]): Promise<SubmitResult> {
  const res = await mutate("/downloads", "POST", { urls });
  const parsed = SubmitResultSchema.safeParse(res);
  if (!parsed.success) throw new ApiError("schema mismatch: /downloads", res ? 200 : 0, "bad-payload");
  return parsed.data;
}

export async function deleteDownload(id: string): Promise<void> {
  await mutate(`/downloads/${encodeURIComponent(id)}`, "DELETE");
}

export async function retryDownload(id: string): Promise<void> {
  await mutate(`/downloads/${encodeURIComponent(id)}/retry`, "POST");
}

export async function convertDownload(id: string, target: string): Promise<void> {
  await mutate(`/downloads/${encodeURIComponent(id)}/convert`, "POST", { target });
}

export const downloadThumbnailPath = (id: string) =>
  `/api/v1/downloads/${encodeURIComponent(id)}/thumbnail`;

async function mutate(path: string, method: "POST" | "DELETE", body?: unknown): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(apiPath(path), {
      method,
      cache: "no-store",
      credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new ApiError(`network: ${path}`, 0, "unavailable");
  }
  if (res.status === 401) throw new ApiError(`signed out: ${path}`, 401, "signed-out");
  if (res.status === 404) throw new ApiError(`not found: ${path}`, 404, "not-found");
  if (res.status === 204) return null;
  if (!res.ok) {
    const text = await res.json().catch(() => null);
    throw new ApiError(`${method} ${path}: ${text?.error ?? res.status}`, res.status, "unavailable");
  }
  return res.json().catch(() => null);
}
