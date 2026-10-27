import "server-only";
import { execFile, spawn, type ChildProcess } from "node:child_process";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { jobDir, mediaTypeFor, DownloadStore } from "./store";
import { assertPublicHost, type Lookup } from "./validate";

// The downloader itself: an in-process FIFO in place of cc-downloader's
// BullMQ+Redis worker (this deployment has no Redis — one server process is the
// queue). yt-dlp is invoked with execFile/spawn and an ARGUMENT ARRAY ONLY, never
// a shell string, so a URL can never become a command (cc-downloader's rule too).
//
// Each job runs two invocations, the same as the reference worker:
//   1. --dump-json   the media's own metadata (title, extractor, declared size)
//   2. the download  --newline progress lines parsed into the job's progress
// Bound by the tool, not just by us: --max-filesize makes yt-dlp itself refuse an
// oversize file, and the process is killed at YTDLP_TIMEOUT_MS either way.

export interface RunnerOptions {
  store: DownloadStore;
  ytdlpPath: string;
  timeoutMs: number;
  maxBytes: number;
  concurrency: number;
  /** Test seam: the process spawner. Defaults to node:child_process.spawn. */
  spawnImpl?: typeof spawn;
  /** Test seam: the --dump-json call. */
  probeImpl?: (id: string, url: string) => Promise<ProbeResult>;
  /** Test seam: the DNS re-check before each run. Defaults to real resolution. */
  lookupImpl?: Lookup;
  /** ffmpeg binary for post-download conversion. */
  ffmpegPath?: string;
  /** Hours before a finished job's files are swept; 0 keeps them forever. */
  ttlHours?: number;
}

export interface ProbeResult {
  title: string | null;
  extractor: string | null;
  fileSize: number | null;
}

const PROBE_TIMEOUT_MS = 60_000;
const PROBE_BUFFER = 16 * 1024 * 1024;

// [download]  42.0% of ~  12.34MiB at    1.23MiB/s ETA 00:05
const PROGRESS = /\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+)\s*([KMGT]?i?B)\s+at\s+([\d.]+)\s*([KMGT]?i?B)\/s/;
const DESTINATION = /\[download\] Destination: (.+)/;
const MERGED = /\[Merger\] Merging formats into "([^"]+)"/;
const ALREADY = /\[download\] (.+) has already been downloaded/;

const UNIT: Record<string, number> = { B: 1, KiB: 1024, MiB: 1024 ** 2, GiB: 1024 ** 3, TiB: 1024 ** 4 };

/** A yt-dlp size token ("12.34MiB") as bytes; null when the token is absent. */
export function sizeToBytes(n: string | undefined, unit: string | undefined): number | null {
  if (!n || !unit) return null;
  const scale = UNIT[unit];
  return scale ? Math.round(parseFloat(n) * scale) : null;
}

export class DownloadRunner {
  readonly store: DownloadStore;
  private queue: string[] = [];
  private running = new Map<string, ChildProcess>();
  // Claimed at dequeue time, not at spawn: the probe runs between the two, and
  // counting only spawned children would let pump start the whole queue at once.
  private inFlight = new Set<string>();
  private cancelled = new Set<string>();
  private converting = new Set<string>();

  isConverting(id: string): boolean {
    return this.converting.has(id);
  }

  constructor(private opts: RunnerOptions) {
    this.store = opts.store;
  }

  /** Queue a stored job; picks up work left queued by a previous process too. */
  enqueue(id: string): void {
    // inFlight, not running: a claimed job holds its slot from dequeue to the
    // end of run(), including the probe gap where no child exists yet.
    if (this.queue.includes(id) || this.inFlight.has(id)) return;
    this.queue.push(id);
    void this.pump();
  }

  requeueStored(): void {
    for (const job of this.opts.store.list()) {
      if (job.status === "queued") this.enqueue(job.id);
    }
  }

  /** Stop a job: a queued one simply never starts; a running one's process is
   * killed and its .part files stay for a later retry. */
  cancel(id: string): void {
    // Mark unconditionally: a job still probing has a live execFile child too
    // (it sits in `running` the same as a download), and one mid-lifecycle must
    // not spawn its download after being deleted.
    this.cancelled.add(id);
    this.queue = this.queue.filter((q) => q !== id);
    this.running.get(id)?.kill("SIGTERM");
  }

  private async pump(): Promise<void> {
    while (this.queue.length && this.inFlight.size < this.opts.concurrency) {
      const id = this.queue.shift()!;
      const job = this.opts.store.get(id);
      if (!job || job.status !== "queued" || this.cancelled.has(id)) continue;
      this.inFlight.add(id);
      void this.run(id).finally(() => {
        this.inFlight.delete(id);
        // A retry that landed while the job was still finishing left it queued
        // but unqueued (enqueue refused the id still claimed) — admit it now.
        const job = this.opts.store.get(id);
        if (job?.status === "queued" && !this.queue.includes(id)) this.queue.push(id);
        void this.pump();
      });
    }
  }

  private async run(id: string): Promise<void> {
    const { store } = this.opts;
    const job = store.get(id);
    if (!job) return;
    const dir = jobDir(store.root, id);
    await mkdir(dir, { recursive: true });

    try {
      // Mark probing first: "queued" at catch-time must mean a retry landed
      // mid-flight, not the initial state a pre-start failure would preserve.
      store.update(id, { status: "probing", error: null });
      // The submit-time check is hours stale by the time a queued job runs:
      // resolve the host again, refuse anything that now answers internal.
      const host = await assertPublicHost(job.url, this.opts.lookupImpl);
      if (!host.ok) throw new Error(`refused: ${host.error}`);
      // The DNS await has no child to kill — a delete landing inside it must
      // still stop the probe from ever spawning.
      if (this.cancelled.has(id)) throw new Error("cancelled");
      const info = await this.probe(id, job.url);
      if (info.fileSize && info.fileSize > this.opts.maxBytes) {
        throw new Error(`declared size exceeds the limit (${info.fileSize} > ${this.opts.maxBytes} bytes)`);
      }
      store.update(id, {
        title: info.title,
        extractor: info.extractor,
        fileSize: info.fileSize,
        progress: { percent: null, totalBytes: info.fileSize, speedBps: null },
      });
      await store.flush();
      if (this.cancelled.has(id)) throw new Error("cancelled");

      store.update(id, { status: "downloading" });
      const fileName = await this.download(id, job.url, dir);
      const file = path.join(dir, fileName);
      const bytes = (await stat(file)).size;
      store.update(id, {
        status: "done",
        fileName,
        fileSize: bytes,
        mediaType: mediaTypeFor(path.extname(fileName).slice(1)),
        progress: { percent: 100, totalBytes: bytes, speedBps: null },
        error: null,
      });
    } catch (err) {
      // A retry that landed while the job was still finishing re-marked it
      // queued — keep that, and pump's finally re-admits it. Anything else is a
      // real failure.
      if (store.get(id)?.status !== "queued") {
        store.update(id, {
          status: "failed",
          error: err instanceof Error ? err.message.slice(0, 500) : String(err).slice(0, 500),
        });
      }
    } finally {
      this.running.delete(id);
      this.cancelled.delete(id);
      await store.flush();
    }
  }

  private async probe(id: string, url: string): Promise<ProbeResult> {
    if (this.opts.probeImpl) return this.opts.probeImpl(id, url);
    let info: string;
    try {
      info = await new Promise<string>((resolve, reject) => {
        // execFile with a callback, not promisified: the child must sit in
        // `running` so a delete mid-probe can kill it like any download.
        const child = execFile(
          this.opts.ytdlpPath,
          ["--dump-json", "--no-playlist", "--no-warnings", url],
          { timeout: PROBE_TIMEOUT_MS, maxBuffer: PROBE_BUFFER, killSignal: "SIGKILL" },
          (err, stdout) => (err ? reject(err) : resolve(stdout)),
        );
        this.running.set(id, child);
      });
    } catch (err) {
      // A failed probe is the download failing early, not "there is no video".
      const msg = err instanceof Error ? err.message.split("\n").pop() : String(err);
      throw new Error(`probe failed: ${msg}`);
    } finally {
      // The probe finished before its download spawns, so the slot clears here;
      // download() claims `running` again for its own child.
      this.running.delete(id);
    }
    try {
      const parsed = JSON.parse(info) as {
        title?: string;
        extractor?: string;
        filesize?: number;
        filesize_approx?: number;
      };
      return {
        title: parsed.title ?? null,
        extractor: parsed.extractor ?? null,
        fileSize: parsed.filesize ?? parsed.filesize_approx ?? null,
      };
    } catch {
      throw new Error("probe failed: unreadable metadata");
    }
  }

  private download(id: string, url: string, dir: string): Promise<string> {
    const { store } = this.opts;
    const out = path.join(dir, "download.%(ext)s");
    const args = [
      "-f",
      "bv*+ba/b",
      "--merge-output-format",
      "mp4",
      // Ask yt-dlp for the site's own cover art (jpg) — a frame grab via ffmpeg
      // is slower and usually uglier than the thumbnail the page advertises.
      "--write-thumbnail",
      "--convert-thumbnails",
      "jpg",
      // Its own template INSIDE the job dir so a .jpg payload and a thumbnail
      // never share a name and jobs cannot collide on a working-dir file.
      "-o",
      `thumbnail:${dir}/download-thumb.%(ext)s`,
      "--no-playlist",
      "--newline",
      "--no-warnings",
      "--max-filesize",
      String(this.opts.maxBytes),
      "-o",
      out,
      url,
    ];
    const spawnImpl = this.opts.spawnImpl ?? spawn;

    return new Promise((resolve, reject) => {
      const child = spawnImpl(this.opts.ytdlpPath, args, { stdio: ["ignore", "pipe", "pipe"] });
      this.running.set(id, child);

      let fileName: string | null = null;
      let stderrTail = "";
      let killed = false;
      const timer = setTimeout(() => {
        killed = true;
        child.kill("SIGKILL");
      }, this.opts.timeoutMs);
      timer.unref?.();

      // Chunks split wherever the pipe felt like it: buffer the partial tail
      // and only score complete lines, or a Destination/Merger line cut in two
      // leaves a finished download reporting "finished without a file name".
      let tail = "";
      const onLine = (line: string) => {
        const named = DESTINATION.exec(line) ?? MERGED.exec(line) ?? ALREADY.exec(line);
        if (named?.[1]) fileName = path.basename(named[1].trim());
        const m = PROGRESS.exec(line);
        if (m) {
          store.update(id, {
            progress: {
              percent: Math.min(100, parseFloat(m[1])),
              totalBytes: sizeToBytes(m[2], m[3]),
              speedBps: sizeToBytes(m[4], m[5]),
            },
          });
        }
      };
      const feed = (chunk: Buffer) => {
        const text = tail + chunk.toString("utf-8");
        const lines = text.split("\n");
        tail = lines.pop() ?? "";
        for (const line of lines) onLine(line);
      };
      child.stdout?.on("data", feed);
      child.stderr?.on("data", (chunk: Buffer) => {
        stderrTail = (stderrTail + chunk.toString("utf-8")).slice(-2000);
      });

      child.on("error", (err) => {
        clearTimeout(timer);
        reject(new Error(`yt-dlp could not start: ${err.message}`));
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        // The last line may arrive without a trailing newline.
        if (tail) onLine(tail);
        if (this.cancelled.has(id)) return reject(new Error("cancelled"));
        if (killed || code === null) return reject(new Error(`exceeded the ${Math.round(this.opts.timeoutMs / 60000)} minute limit`));
        if (code !== 0) {
          const tail = stderrTail.trim().split("\n").pop() ?? "";
          return reject(new Error(tail || `yt-dlp exited with code ${code}`));
        }
        if (!fileName) return reject(new Error("finished without a file name"));
        resolve(fileName);
      });
    });
  }

  /** Transcode a finished download with ffmpeg. The output lands beside the
   * original as <base>.<target>; service.ts decides which targets a kind may
   * take, this method only refuses work that cannot exist. */
  convert(id: string, fileName: string, target: string): Promise<void> {
    if (this.converting.has(id)) return Promise.reject(new Error("already converting"));
    const dir = jobDir(this.opts.store.root, id);
    const input = path.join(dir, fileName);
    if (path.dirname(path.resolve(input)) !== path.resolve(dir)) {
      return Promise.reject(new Error("bad file name"));
    }
    const base = path.basename(fileName, path.extname(fileName));
    const output = path.join(dir, `${base}.${target}`);
    // ffmpeg writes a staging name and success renames it into place — a killed
    // or failed run can never leave a half-written file that looks finished,
    // and a retry can't clobber a previous good output. The staging name keeps
    // the target extension LAST: ffmpeg picks its muxer from the filename.
    const staging = path.join(dir, `${base}.converting.${target}`);
    this.converting.add(id);
    const spawnImpl = this.opts.spawnImpl ?? spawn;
    return new Promise<void>((resolve, reject) => {
      const args = ffmpegArgs(target);
      let child: ChildProcess;
      try {
        child = spawnImpl(this.opts.ffmpegPath ?? "ffmpeg", ["-hide_banner", "-y", "-i", input, ...args, staging], {
          stdio: ["ignore", "ignore", "pipe"],
        });
      } catch (err) {
        this.converting.delete(id);
        return reject(new Error(`ffmpeg could not start: ${err instanceof Error ? err.message : String(err)}`));
      }
      this.running.set(id, child);
      let stderrTail = "";
      child.stderr?.on("data", (c: Buffer) => {
        stderrTail = (stderrTail + c.toString("utf-8")).slice(-2000);
      });
      let killed = false;
      const timer = setTimeout(() => {
        killed = true;
        child.kill("SIGKILL");
      }, this.opts.timeoutMs);
      timer.unref?.();
      // Ownership holds through publish AND cleanup: until `converting` clears,
      // sweep cannot expire the job and a second convert cannot claim the same
      // staging path, so the rm/rename below cannot race a follow-up run.
      let settled = false;
      const finish = async (msg?: string) => {
        // error can precede close on the same child — first entry wins.
        if (settled) return;
        settled = true;
        try {
          if (this.running.get(id) === child) this.running.delete(id);
          if (msg) throw new Error(msg);
          // A delete that landed between close and here must not publish.
          if (this.cancelled.has(id)) throw new Error("cancelled");
          await rename(staging, output);
          const job = this.opts.store.get(id);
          if (!job) {
            // Deleted mid-publish: the job dir is on its way out; never report
            // success for a file nobody can reach.
            await rm(output, { force: true }).catch(() => {});
            throw new Error("deleted");
          }
          // Publishing refreshes the retention clock: the 24h window starts
          // from the last thing that touched the file, not the download.
          this.opts.store.update(id, {});
          resolve();
        } catch (err) {
          await rm(staging, { force: true }).catch(() => {});
          reject(err instanceof Error ? err : new Error(String(err)));
        } finally {
          this.converting.delete(id);
        }
      };
      child.on("error", (err) => {
        clearTimeout(timer);
        void finish(`ffmpeg could not start: ${err.message}`);
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        if (this.cancelled.has(id)) return void finish("cancelled");
        if (killed || code === null) return void finish(`exceeded the ${Math.round(this.opts.timeoutMs / 60000)} minute limit`);
        if (code !== 0) {
          const tail = stderrTail.trim().split("\n").pop() ?? "";
          return void finish(tail || `ffmpeg exited with code ${code}`);
        }
        void finish();
      });
    });
  }

  /** Remove done/failed jobs older than the retention window. Called on boot
   * and by the sweep timer; active jobs and in-flight conversions are never
   * touched (a converting job keeps status "done", so check the set). */
  async sweep(ttlHours = this.opts.ttlHours ?? 0): Promise<number> {
    if (!ttlHours) return 0;
    const cutoff = Date.now() - ttlHours * 3_600_000;
    let removed = 0;
    for (const job of this.opts.store.list()) {
      if (job.status !== "done" && job.status !== "failed") continue;
      if (this.converting.has(job.id)) continue;
      if (new Date(job.updatedAt).getTime() >= cutoff) continue;
      this.cancel(job.id);
      try {
        if (await this.opts.store.remove(job.id)) removed++;
      } catch {
        // A failed removal stays in the registry for the next sweep; the count
        // only reports jobs actually gone.
      }
    }
    return removed;
  }
}

/** ffmpeg's codec flag for each target. Audio targets drop the video stream;
 * image targets take a single frame. */
function ffmpegArgs(target: string): string[] {
  switch (target) {
    case "mp3": return ["-vn", "-c:a", "libmp3lame", "-q:a", "3"];
    case "m4a": return ["-vn", "-c:a", "aac", "-b:a", "192k"];
    case "opus": return ["-vn", "-c:a", "libopus", "-b:a", "160k"];
    case "wav": return ["-vn", "-c:a", "pcm_s16le"];
    case "png": return ["-frames:v", "1"];
    case "jpg": return ["-frames:v", "1", "-q:v", "2"];
    case "webp": return ["-frames:v", "1"];
    case "mp4": return ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"];
    case "webm": return ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"];
    case "mkv": return ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "192k"];
    default: return [];
  }
}

// The one runner the process shares. Route handlers come and go (and in dev the
// module reloads); the queue and its children must not, so they live on
// globalThis — the same seam the session's identity.toml cache uses, and the
// standard answer to Next.js module duplication.
const GLOBAL_KEY = "__nora_download_runner__";

export interface RunnerEnv {
  NORA_DOWNLOADS_DIR?: string;
  YTDLP_PATH?: string;
  YTDLP_TIMEOUT_MS?: string;
  NORA_DOWNLOADS_MAX_BYTES?: string;
  NORA_DOWNLOADS_CONCURRENCY?: string;
  FFMPEG_PATH?: string;
  NORA_DOWNLOADS_TTL_HOURS?: string;
}

export function downloadRunner(env: RunnerEnv = process.env as RunnerEnv): DownloadRunner {
  const g = globalThis as unknown as Record<string, DownloadRunner | undefined>;
  if (!g[GLOBAL_KEY]) {
    // The image sets both env vars; under `next dev` the defaults are the
    // repo's dev/downloads (gitignored) and whatever yt-dlp is on PATH.
    const root =
      env.NORA_DOWNLOADS_DIR ??
      (process.env.NODE_ENV === "production" ? "/data/downloads" : "dev/downloads");
    const store = DownloadStore.load(root);
    const runner = new DownloadRunner({
      store,
      ytdlpPath: env.YTDLP_PATH ?? "yt-dlp",
      timeoutMs: Math.max(60_000, Number(env.YTDLP_TIMEOUT_MS ?? 3_600_000) || 3_600_000),
      maxBytes: Math.max(1, Number(env.NORA_DOWNLOADS_MAX_BYTES ?? 5_368_709_120) || 5_368_709_120),
      concurrency: Math.max(1, Math.min(8, Number(env.NORA_DOWNLOADS_CONCURRENCY ?? 2) || 2)),
      ffmpegPath: env.FFMPEG_PATH ?? "ffmpeg",
      ttlHours: Math.max(0, Number(env.NORA_DOWNLOADS_TTL_HOURS ?? 24) || 0),
    });
    runner.requeueStored();
    // Retention sweep: once at boot, then hourly. Operator-asked window is
    // 24h; NORA_DOWNLOADS_TTL_HOURS=0 keeps files forever.
    void runner.sweep();
    const t = setInterval(() => void runner.sweep(), 3_600_000);
    t.unref?.();
    g[GLOBAL_KEY] = runner;
  }
  return g[GLOBAL_KEY];
}
