import "server-only";
import { z } from "zod";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileHeaders } from "../artifact-download";
import { assertPublicHost, validateBatch, type Lookup } from "./validate";
import { JOB_ID, jobDir, listJobFiles, mediaKind, mediaTypeFor, thumbPath, type DownloadJob } from "./store";
import type { DownloadRunner } from "./runner";

// The route-level rules for /api/v1/downloads/*, kept apart from the route
// files so they run under vitest without a server (the same split as
// artifact-download.ts / its route.ts).

const MAX_BATCH = 50;

export const SubmitSchema = z.object({
  urls: z.array(z.string().max(2048)).min(1).max(MAX_BATCH),
});

export interface Rejected {
  url: string;
  error: string;
  existingId?: string;
}

export async function submit(
  runner: DownloadRunner,
  personId: string,
  urls: string[],
  lookup?: Lookup,
): Promise<{ status: number; body: { jobs: DownloadJob[]; rejected: Rejected[] } }> {
  const { accepted, rejected: checked } = validateBatch(urls, MAX_BATCH);
  const rejected: Rejected[] = checked;
  const jobs: DownloadJob[] = [];
  for (const url of accepted) {
    // Literal-hostname validation is not enough: a public name can resolve to a
    // private address. Refuse it here, and again at run start in the runner.
    const host = await assertPublicHost(url, lookup);
    if (!host.ok) {
      rejected.push({ url, error: host.error });
      continue;
    }
    const existing = runner.store.findActive(url);
    if (existing) {
      rejected.push({ url, error: "exists", existingId: existing.id });
      continue;
    }
    const job = runner.store.create(url, personId);
    runner.enqueue(job.id);
    jobs.push(job);
  }
  return { status: jobs.length ? 201 : 200, body: { jobs, rejected } };
}

export interface JobView extends DownloadJob {
  files: string[];
  hasThumb: boolean;
  converting: boolean;
  convertTargets: string[];
}

const CONVERT_TARGETS = {
  video: ["mp4", "webm", "mkv"],
  audio: ["mp3", "m4a", "opus", "wav"],
  image: ["png", "jpg", "webp"],
} as const;

export async function jobView(runner: DownloadRunner, job: DownloadJob): Promise<JobView> {
  const kind = mediaKind(job);
  const cur = path.extname(job.fileName ?? "").slice(1).toLowerCase();
  return {
    ...job,
    files: await listJobFiles(runner.store.root, job),
    hasThumb: (await thumbPath(runner.store.root, job)) !== null,
    converting: runner.isConverting(job.id),
    convertTargets: kind ? CONVERT_TARGETS[kind].filter((t) => t !== cur) : [],
  };
}

export async function list(runner: DownloadRunner): Promise<{ jobs: JobView[]; usedBytes: number }> {
  const jobs = runner.store.list();
  return {
    jobs: await Promise.all(jobs.map((j) => jobView(runner, j))),
    usedBytes: jobs.reduce((n, j) => n + (j.fileSize ?? 0), 0),
  };
}

export async function get(runner: DownloadRunner, id: string): Promise<{ status: number; body: unknown }> {
  const job = runner.store.get(id);
  if (!job) return { status: 404, body: { error: "not found" } };
  return { status: 200, body: { job: await jobView(runner, job) } };
}

/** Start an ffmpeg conversion on a finished job. The output lands next to the
 * original and shows up in the job's files list. */
export async function convert(runner: DownloadRunner, id: string, target: string): Promise<{ status: number; body: unknown }> {
  const job = runner.store.get(id);
  if (!job || !JOB_ID.test(id)) return { status: 404, body: { error: "not found" } };
  if (job.status !== "done" || !job.fileName) return { status: 409, body: { error: "not finished" } };
  const kind = mediaKind(job);
  const allowed = kind ? (CONVERT_TARGETS[kind] as readonly string[]) : [];
  if (!allowed.includes(target)) return { status: 400, body: { error: `cannot convert to ${target}` } };
  try {
    await runner.convert(id, job.fileName, target);
  } catch (err) {
    return { status: 409, body: { error: err instanceof Error ? err.message : String(err) } };
  }
  const after = runner.store.get(id);
  if (!after) return { status: 404, body: { error: "deleted" } };
  return { status: 200, body: { job: await jobView(runner, after) } };
}

/** The job's thumbnail for the list's preview cell. */
export async function serveThumbnail(runner: DownloadRunner, id: string): Promise<Response> {
  const job = runner.store.get(id);
  if (!job || !JOB_ID.test(id)) return json(404, { error: "not found" });
  const thumb = await thumbPath(runner.store.root, job);
  if (!thumb) return json(404, { error: "not found" });
  const ext = path.extname(thumb).slice(1);
  const stream = Readable.toWeb(createReadStream(thumb)) as ReadableStream;
  return new Response(stream, {
    status: 200,
    headers: { ...fileHeaders(`image/${ext === "jpg" ? "jpeg" : ext}`, `thumb.${ext}`), "content-length": String((await stat(thumb)).size) },
  });
}

export function retry(runner: DownloadRunner, id: string): { status: number; body: unknown } {
  const job = runner.store.get(id);
  if (!job) return { status: 404, body: { error: "not found" } };
  if (job.status !== "failed") return { status: 409, body: { error: "not failed" } };
  runner.store.update(id, {
    status: "queued",
    error: null,
    progress: { percent: null, totalBytes: null, speedBps: null },
  });
  runner.enqueue(id);
  return { status: 200, body: { job: runner.store.get(id) } };
}

export async function remove(runner: DownloadRunner, id: string): Promise<{ status: number; body: unknown }> {
  runner.cancel(id);
  return (await runner.store.remove(id)) ? { status: 204, body: null } : { status: 404, body: { error: "not found" } };
}

const BYTE_RANGE = /^bytes=(\d*)-(\d*)$/;

/** The bytes of a finished job, with the one-range reads a <video> tag and
 * Safari need (artifact-download.ts plays the same game for Nora's files). */
export async function serveContent(
  runner: DownloadRunner,
  id: string,
  rangeHeader: string | null,
  file?: string | null,
): Promise<Response> {
  const job = runner.store.get(id);
  if (!job || !JOB_ID.test(id)) return json(404, { error: "not found" });
  let filePath: string | null;
  if (file) {
    // A converted output can be fetched by name — but only names the dir scan
    // itself reports, so the param can never leave the job dir.
    const files = await listJobFiles(runner.store.root, job);
    filePath = files.includes(file) ? path.join(jobDir(runner.store.root, id), file) : null;
  } else {
    filePath = await runner.store.filePath(id);
  }
  if (!filePath) return json(404, { error: "not found" });

  const size = (await stat(filePath)).size;
  const ext = path.extname(filePath).slice(1);
  const name = safeFileName(job, ext, path.basename(filePath));
  // A converted output takes its own extension's type — a webm made from an
  // mp4 must not keep the job's video/mp4 label.
  const type = file ? mediaTypeFor(ext) : (job.mediaType ?? "application/octet-stream");
  const headers = { ...fileHeaders(type, name), "accept-ranges": "bytes" };

  const m = rangeHeader && BYTE_RANGE.exec(rangeHeader);
  let start = 0;
  let end = size - 1;
  if (m && (m[1] !== "" || m[2] !== "")) {
    if (m[1] === "") {
      // Suffix form: the last N bytes.
      const n = parseInt(m[2], 10);
      start = Math.max(0, size - n);
    } else {
      start = parseInt(m[1], 10);
      if (m[2] !== "") end = Math.min(size - 1, parseInt(m[2], 10));
    }
    if (start > end || start >= size) {
      return new Response(null, {
        status: 416,
        headers: { ...headers, "content-range": `bytes */${size}` },
      });
    }
    const stream = Readable.toWeb(createReadStream(filePath, { start, end })) as ReadableStream;
    return new Response(stream, {
      status: 206,
      headers: { ...headers, "content-range": `bytes ${start}-${end}/${size}`, "content-length": String(end - start + 1) },
    });
  }

  const stream = Readable.toWeb(createReadStream(filePath)) as ReadableStream;
  return new Response(stream, { status: 200, headers: { ...headers, "content-length": String(size) } });
}

/** The download name the browser is offered: the media's title when it makes a
 * safe ASCII filename, else the name of the file actually being served — a
 * converted output must not borrow the original's name and extension. */
function safeFileName(job: DownloadJob, ext: string, servedName?: string): string {
  const extName = ext || "bin";
  const title = (job.title ?? "")
    .normalize("NFKD")
    .replace(/[^A-Za-z0-9._ -]+/g, "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, 120);
  return title ? `${title}.${extName}` : (servedName ?? `download.${extName}`);
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "private, no-store" },
  });
}
