import "server-only";
import { randomBytes } from "node:crypto";
import { mkdirSync, readFileSync } from "node:fs";
import { writeFile, rename, rm, readdir } from "node:fs/promises";
import path from "node:path";

// The download registry, on disk. cc-downloader keeps jobs in Postgres and files in
// MinIO; this app has neither, so the registry is one JSON file next to the files it
// describes — atomic rename so a crash mid-write never leaves a torn registry, and the
// whole library is the one bind-mounted directory an operator can back up or wipe.
// # ponytail: every job in one file; at this volume (a circle's downloads) that is a
// few KB. If it ever contends, per-job files are the upgrade.

export const STATUSES = ["queued", "probing", "downloading", "done", "failed"] as const;
export type DownloadStatus = (typeof STATUSES)[number];

export interface DownloadJob {
  id: string;
  url: string;
  status: DownloadStatus;
  progress: { percent: number | null; totalBytes: number | null; speedBps: number | null };
  title: string | null;
  extractor: string | null;
  fileName: string | null;
  fileSize: number | null;
  mediaType: string | null;
  error: string | null;
  /** The identity.toml person id who submitted it (the library is the circle's). */
  submittedBy: string;
  createdAt: string;
  updatedAt: string;
}

export const JOB_ID = /^[0-9a-f]{24}$/;

interface RegistryFile {
  jobs: DownloadJob[];
}

const REGISTRY = "jobs.json";

/** One download's directory inside the root: the media file plus yt-dlp's
 * transient parts, all deleted together. */
export function jobDir(root: string, id: string): string {
  return path.join(root, "files", id);
}

/** Where a finished job's payload sits; null while it has none. */
export function jobFile(root: string, job: DownloadJob): string | null {
  if (!job.fileName) return null;
  const file = path.join(jobDir(root, job.id), job.fileName);
  // The stored name came from this process's own readdir, never from a client,
  // but stay fail-closed anyway: the resolved path must sit inside the job dir.
  return path.dirname(file) === path.join(root, "files", job.id) ? file : null;
}

const MEDIA_TYPES: Record<string, string> = {
  mp4: "video/mp4",
  webm: "video/webm",
  mkv: "video/x-matroska",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  opus: "audio/ogg",
  ogg: "audio/ogg",
  wav: "audio/wav",
  flac: "audio/flac",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
};

export function mediaTypeFor(ext: string): string {
  return MEDIA_TYPES[ext.toLowerCase()] ?? "application/octet-stream";
}

export class DownloadStore {
  private jobs = new Map<string, DownloadJob>();
  private dirty = false;
  private flushing: Promise<void> | null = null;
  // Every write goes through this one chain: two jobs finishing together each
  // await flush(), and two writes to jobs.json.tmp would otherwise interleave
  // (one rename pulls the tmp out from under the other).
  private chain: Promise<void> = Promise.resolve();

  private constructor(readonly root: string) {}

  static load(root: string): DownloadStore {
    const store = new DownloadStore(root);
    mkdirSync(root, { recursive: true });
    try {
      const parsed = JSON.parse(readFileSync(path.join(root, REGISTRY), "utf-8")) as RegistryFile;
      for (const job of parsed.jobs ?? []) store.jobs.set(job.id, job);
    } catch {
      // No registry yet, or one torn beyond repair: an absent file is an empty
      // library, never a crash loop.
    }
    // Anything in flight when the process last stopped is not running now:
    // re-queue it so the runner picks it up and yt-dlp resumes its .part file.
    for (const job of store.jobs.values()) {
      if (job.status === "probing" || job.status === "downloading") {
        job.status = "queued";
        job.error = null;
        job.progress = { percent: null, totalBytes: null, speedBps: null };
        job.updatedAt = new Date().toISOString();
      }
    }
    return store;
  }

  list(): DownloadJob[] {
    return [...this.jobs.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  get(id: string): DownloadJob | null {
    return this.jobs.get(id) ?? null;
  }

  /** The job already holding this URL in a live or finished state, for dedup. */
  findActive(url: string): DownloadJob | null {
    for (const job of this.jobs.values()) {
      if (job.url === url && job.status !== "failed") return job;
    }
    return null;
  }

  create(url: string, submittedBy: string): DownloadJob {
    const now = new Date().toISOString();
    const job: DownloadJob = {
      id: randomBytes(12).toString("hex"),
      url,
      status: "queued",
      progress: { percent: null, totalBytes: null, speedBps: null },
      title: null,
      extractor: null,
      fileName: null,
      fileSize: null,
      mediaType: null,
      error: null,
      submittedBy,
      createdAt: now,
      updatedAt: now,
    };
    this.jobs.set(job.id, job);
    this.markDirty();
    return job;
  }

  update(id: string, patch: Partial<Omit<DownloadJob, "id" | "createdAt">>): DownloadJob | null {
    const job = this.jobs.get(id);
    if (!job) return null;
    Object.assign(job, patch, { updatedAt: new Date().toISOString() });
    this.markDirty();
    return job;
  }

  /** Forget the job and its files. Kill a live process first (the runner does). */
  async remove(id: string): Promise<boolean> {
    const job = this.jobs.get(id);
    if (!job) return false;
    this.jobs.delete(id);
    this.markDirty();
    await this.flush();
    await rm(jobDir(this.root, id), { recursive: true, force: true });
    return true;
  }

  /** A job's media file on disk, when it has landed; resolves the name if the
   * record lost it (a probe that never reached the rename). */
  async filePath(id: string): Promise<string | null> {
    const job = this.jobs.get(id);
    if (!job || job.status !== "done") return null;
    const dir = jobDir(this.root, id);
    if (job.fileName) {
      const named = jobFile(this.root, job);
      if (named) return named;
    }
    try {
      const files = (await readdir(dir)).filter(
        // Not an in-flight part, and not an unmerged format file (download.f137.mp4).
        (f) => !f.endsWith(".part") && !f.endsWith(".ytdl") && !/^download\.f\d+\./.test(f),
      );
      const merged = files.find((f) => /^download\.[^.]+$/.test(f));
      const named = merged ?? files[0];
      return named ? path.join(dir, named) : null;
    } catch {
      return null;
    }
  }

  private markDirty(): void {
    this.dirty = true;
    if (!this.flushing) {
      // Persist on the next turn: progress ticks at many-per-second during a
      // download, and a state a beat old on disk is fine — only a job reaching
      // or leaving a state has to be durable at once (callers await flush()).
      // A failed write stays dirty so the next update retries it, and logs —
      // an unhandled rejection would be a crash, a silent one a lost registry.
      this.flushing = this.flush()
        .catch((e) => console.error("[downloads] registry write failed:", e instanceof Error ? e.message : e))
        .finally(() => {
          this.flushing = null;
          // An update that landed during the write is still dirty: persist again.
          if (this.dirty) this.markDirty();
        });
    }
  }

  async flush(): Promise<void> {
    const p = this.chain.then(() => this.writeNow());
    this.chain = p.then(
      () => {},
      () => {},
    );
    return p;
  }

  private writes = 0;

  private async writeNow(): Promise<void> {
    if (!this.dirty) return;
    this.dirty = false;
    // Unique tmp name per write: a second store on the same root (tests do it)
    // must not rename another writer's tmp out from under it.
    const tmp = path.join(this.root, `${REGISTRY}.${process.pid}.${this.writes++}.tmp`);
    try {
      await writeFile(tmp, JSON.stringify({ jobs: this.list() } satisfies RegistryFile), "utf-8");
      await rename(tmp, path.join(this.root, REGISTRY));
    } catch (err) {
      // The change was not persisted: stay dirty so the next update retries it
      // rather than reporting a state the disk never learned.
      this.dirty = true;
      throw err;
    }
  }
}
