import "server-only";
import { execFile, spawn, type ChildProcess } from "node:child_process";
import { mkdir, stat } from "node:fs/promises";
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
    });
    runner.requeueStored();
    g[GLOBAL_KEY] = runner;
  }
  return g[GLOBAL_KEY];
}
