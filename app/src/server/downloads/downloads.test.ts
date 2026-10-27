import { EventEmitter } from "node:events";
import { mkdtempSync, writeFileSync, existsSync, rmSync, readFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { ChildProcess } from "node:child_process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { assertPublicHost, isPrivateAddress, validateBatch, validateSubmissionUrl } from "./validate";
import { DownloadStore, mediaTypeFor } from "./store";
import { DownloadRunner, sizeToBytes, type RunnerOptions } from "./runner";

vi.mock("server-only", () => ({}));

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), "nora-dl-"));
});
afterEach(async () => {
  // Flush queue drains on a promise chain: give it a beat before removing the
  // dir, or a scheduled write lands on a path that no longer exists.
  await new Promise((r) => setTimeout(r, 30));
  rmSync(dir, { recursive: true, force: true });
});

// ----------------------------------------------------------------- validate

describe("validateSubmissionUrl", () => {
  it("accepts http and https URLs", () => {
    expect(validateSubmissionUrl("https://www.youtube.com/watch?v=abc123").ok).toBe(true);
    expect(validateSubmissionUrl("http://example.com/file.mp4").ok).toBe(true);
  });

  it("rejects other schemes and non-URLs", () => {
    for (const url of ["ftp://x/f", "file:///etc/passwd", "javascript:alert(1)", "not a url", ""]) {
      expect(validateSubmissionUrl(url).ok).toBe(false);
    }
  });

  it("rejects private, loopback and link-local hosts", () => {
    const bad = [
      "http://127.0.0.1/x",
      "http://localhost/admin",
      "http://10.1.2.3/",
      "http://172.16.0.1/",
      "http://172.31.255.1/",
      "http://192.168.1.1/",
      "http://169.254.169.254/latest/meta-data",
      `http://${[100, 64, 0, 1].join(".")}/`, // CGNAT range (tailnets)
      "http://[::1]/",
      "http://[fc00::1]/",
      "http://[fe80::1]/",
      "http://[fe90::1]/", // fe80::/10 runs through febf
      "http://[::ffff:127.0.0.1]/",
    ];
    for (const url of bad) expect(validateSubmissionUrl(url).ok).toBe(false);
    expect(validateSubmissionUrl("http://172.15.0.1/").ok).toBe(true);
    expect(validateSubmissionUrl("http://172.32.0.1/").ok).toBe(true);
  });

  it("accepts a public IPv6 literal — no dot does not mean a service name", () => {
    expect(validateSubmissionUrl("http://[2001:4860:4860::8888]/v").ok).toBe(true);
  });

  it("rejects internal names: single-label hosts and private TLDs", () => {
    // The hole that was live: a compose service name is not a private IP, so
    // http://nora:9100/status would have passed the literal-IP check alone.
    for (const url of [
      "http://nora:9100/status",
      "http://signal-cli:8080/",
      "http://nas.internal/",
      "http://router.local/",
      "http://prn.lan/",
    ]) {
      const r = validateSubmissionUrl(url);
      expect(r.ok, url).toBe(false);
      if (!r.ok) expect(r.error).toBe("private");
    }
  });
});

describe("assertPublicHost (DNS-level SSRF bound)", () => {
  const publicIp = { address: "93.184.216.34" };

  it("refuses a public name that resolves to a private address", async () => {
    const r = await assertPublicHost("https://sneaky.example/", async () => [{ address: "10.9.9.9" }]);
    expect(r).toEqual({ ok: false, error: "private" });
  });

  it("refuses when ANY resolved address is internal (round-robin rebinding)", async () => {
    const r = await assertPublicHost("https://sneaky.example/", async () => [publicIp, { address: "169.254.10.10" }]);
    expect(r.ok).toBe(false);
  });

  it("refuses when the name cannot be resolved — could not check is never a pass", async () => {
    const r = await assertPublicHost("https://nx.example/", async () => {
      throw new Error("ENOTFOUND");
    });
    expect(r).toEqual({ ok: false, error: "unresolvable" });
  });

  it("refuses an empty answer set", async () => {
    const r = await assertPublicHost("https://empty.example/", async () => []);
    expect(r.ok).toBe(false);
  });

  it("accepts a public resolution", async () => {
    const r = await assertPublicHost("https://media.example/", async () => [publicIp]);
    expect(r.ok).toBe(true);
  });

  it("checks literal IPs without asking DNS at all", async () => {
    const lookup = vi.fn(async () => [publicIp]);
    expect((await assertPublicHost("http://192.168.0.5/x", lookup)).ok).toBe(false);
    expect(lookup).not.toHaveBeenCalled();
    expect((await assertPublicHost("http://8.8.8.8/dns", lookup)).ok).toBe(true);
  });
});

describe("isPrivateAddress", () => {
  it("covers v4 private/loopback/link-local/CGNAT/reserved and v6 local scopes", () => {
    for (const a of ["0.0.0.0", "10.0.0.1", "127.0.0.1", "169.254.1.1", "172.16.0.1", "192.168.0.1", [100, 64, 5, 5].join("."), "224.0.0.1", "::1", "::", "::ffff:8.8.8.8", "fe80::1", "febf::1", "fc00::1", "fd12::1"]) {
      expect(isPrivateAddress(a), a).toBe(true);
    }
    for (const a of ["8.8.8.8", "93.184.216.34", "172.15.0.1", "100.63.0.1", "2001:4860:4860::8888"]) {
      expect(isPrivateAddress(a), a).toBe(false);
    }
  });
});

describe("validateBatch", () => {
  it("accepts good URLs, rejects bad ones with reasons, dedupes", () => {
    const { accepted, rejected } = validateBatch(
      ["https://a.example/v1", "not a url", "https://a.example/v1", "http://10.0.0.1/x", "  https://b.example/v2  "],
      50,
    );
    expect(accepted).toEqual(["https://a.example/v1", "https://b.example/v2"]);
    expect(rejected).toHaveLength(3);
    expect(rejected.map((r) => r.error)).toEqual(["invalid", "duplicate", "private"]);
  });
});

// -------------------------------------------------------------------- store

describe("DownloadStore", () => {
  it("creates, lists newest-first, dedupes live jobs, removes with files", async () => {
    const store = DownloadStore.load(dir);
    const a = store.create("https://a.example/1", "miles");
    await store.flush();
    const b = store.create("https://b.example/2", "miles");
    await store.flush();

    expect(store.list().map((j) => j.id)).toEqual([b.id, a.id]);
    expect(store.findActive("https://a.example/1")?.id).toBe(a.id);
    expect(store.get(a.id)?.submittedBy).toBe("miles");

    mkdirSync(path.join(dir, "files", a.id), { recursive: true });
    writeFileSync(path.join(dir, "files", a.id, "download.mp4"), "x");
    expect(await store.remove(a.id)).toBe(true);
    expect(existsSync(path.join(dir, "files", a.id))).toBe(false);
    expect(await store.remove(a.id)).toBe(false);
  });

  it("persists across loads and re-queues jobs that were in flight", async () => {
    const store = DownloadStore.load(dir);
    const job = store.create("https://a.example/1", "miles");
    store.update(job.id, { status: "downloading", progress: { percent: 40, totalBytes: 100, speedBps: 10 } });
    const done = store.create("https://a.example/2", "miles");
    store.update(done.id, { status: "done", fileName: "download.mp4" });
    await store.flush();

    const reloaded = DownloadStore.load(dir);
    expect(reloaded.get(job.id)?.status).toBe("queued");
    expect(reloaded.get(job.id)?.progress.percent).toBeNull();
    expect(reloaded.get(done.id)?.status).toBe("done");
    // A failed job stays failed (it needs a deliberate retry), so dedup frees its URL.
    const failed = reloaded.create("https://a.example/3", "miles");
    reloaded.update(failed.id, { status: "failed", error: "x" });
    expect(reloaded.findActive("https://a.example/3")).toBeNull();
  });

  it("treats a torn registry as an empty library", () => {
    writeFileSync(path.join(dir, "jobs.json"), "{not json");
    const store = DownloadStore.load(dir);
    expect(store.list()).toEqual([]);
  });

  it("resolves the finished file, preferring the merged name over format parts", async () => {
    const store = DownloadStore.load(dir);
    const job = store.create("https://a.example/1", "miles");
    const jd = path.join(dir, "files", job.id);
    mkdirSync(jd, { recursive: true });
    writeFileSync(path.join(jd, "download.f137.mp4"), "v");
    writeFileSync(path.join(jd, "download.f140.m4a"), "a");
    writeFileSync(path.join(jd, "download.mp4"), "av");
    store.update(job.id, { status: "done" });
    expect(await store.filePath(job.id)).toBe(path.join(jd, "download.mp4"));
    store.update(job.id, { status: "downloading" });
    expect(await store.filePath(job.id)).toBeNull();
  });
});

// ------------------------------------------------------------------- runner

interface FakeProc extends EventEmitter {
  stdout: EventEmitter;
  stderr: EventEmitter;
  kill: (signal?: string) => void;
}

/** A yt-dlp that behaves like the binary: reads -o for its output template,
 * writes download.<ext>, streams [download] progress lines, closes 0 — or,
 * given a URL containing "fail", closes 1 with a stderr error like a real
 * extractor error. "hang" URLs never close (for cancel/timeout tests). */
function fakeSpawn(behavior: (args: string[], proc: FakeProc) => void) {
  return vi.fn((cmd: string, args: string[]) => {
    const proc = new EventEmitter() as FakeProc;
    proc.stdout = new EventEmitter();
    proc.stderr = new EventEmitter();
    proc.kill = () => queueMicrotask(() => proc.emit("close", null));
    queueMicrotask(() => behavior(args, proc));
    return proc as unknown as ChildProcess;
  });
}

const goodYtdlp = (args: string[], proc: FakeProc) => {
  const url = args[args.length - 1];
  const outIdx = args.indexOf("-o") + 1;
  const file = args[outIdx].replace("%(ext)s", "mp4");
  if (url.includes("fail")) {
    proc.stderr.emit("data", Buffer.from("ERROR: [generic] fail: no video\n"));
    proc.emit("close", 1);
    return;
  }
  if (url.includes("hang")) return;
  writeFileSync(file, "media-bytes");
  proc.stdout.emit("data", Buffer.from(`[download] Destination: ${file}\n`));
  proc.stdout.emit("data", Buffer.from("[download]  42.0% of ~  10.00MiB at    2.00MiB/s ETA 00:03\n"));
  proc.stdout.emit("data", Buffer.from("[download] 100% of ~  10.00MiB in 00:00:05\n"));
  proc.emit("close", 0);
};

function makeRunner(over: Partial<RunnerOptions> = {}) {
  const store = DownloadStore.load(dir);
  const runner = new DownloadRunner({
    store,
    ytdlpPath: "/bin/false",
    timeoutMs: 60_000,
    maxBytes: 1_000_000,
    concurrency: 2,
    spawnImpl: fakeSpawn(goodYtdlp) as never,
    probeImpl: async (_id, url) => ({ title: `T:${url}`, extractor: "generic", fileSize: 10 }),
    // Tests never touch real DNS: every host answers as one public address.
    lookupImpl: async () => [{ address: "93.184.216.34" }],
    ...over,
  });
  return { store, runner };
}

/** Wait until a job reaches a terminal state (or give up). */
async function settle(store: DownloadStore, id: string): Promise<void> {
  for (let i = 0; i < 200; i++) {
    const s = store.get(id)?.status;
    if (s === "done" || s === "failed") return;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("job never settled");
}

describe("sizeToBytes", () => {
  it("parses yt-dlp size tokens", () => {
    expect(sizeToBytes("10.00", "MiB")).toBe(10 * 1024 * 1024);
    expect(sizeToBytes("1.5", "GiB")).toBe(Math.round(1.5 * 1024 ** 3));
    expect(sizeToBytes("512", "B")).toBe(512);
    expect(sizeToBytes(undefined, "MiB")).toBeNull();
  });
});

describe("mediaTypeFor", () => {
  it("maps extensions, unknown to octet-stream", () => {
    expect(mediaTypeFor("mp4")).toBe("video/mp4");
    expect(mediaTypeFor("flac")).toBe("audio/flac");
    expect(mediaTypeFor("xyz")).toBe("application/octet-stream");
  });
});

describe("DownloadRunner", () => {
  it("runs a job end to end: probe, progress, file landed, done", async () => {
    const { store, runner } = makeRunner();
    const job = store.create("https://media.example/video/1", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);

    const done = store.get(job.id)!;
    expect(done.status).toBe("done");
    expect(done.title).toBe("T:https://media.example/video/1");
    expect(done.fileName).toBe("download.mp4");
    expect(done.fileSize).toBe(11);
    expect(done.mediaType).toBe("video/mp4");
    expect(done.progress.percent).toBe(100);
    expect(await store.filePath(job.id)).toBe(path.join(dir, "files", job.id, "download.mp4"));
    expect(JSON.parse(readFileSync(path.join(dir, "jobs.json"), "utf-8")).jobs).toHaveLength(1);
  });

  it("fails a job whose probe declares a file over the limit, without spawning", async () => {
    const spawnImpl = fakeSpawn(goodYtdlp);
    const { store, runner } = makeRunner({
      maxBytes: 5,
      spawnImpl: spawnImpl as never,
    });
    const job = store.create("https://media.example/big", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("failed");
    expect(store.get(job.id)!.error).toContain("exceeds the limit");
    expect(spawnImpl).not.toHaveBeenCalled();
  });

  it("fails a job when yt-dlp exits non-zero, keeping its stderr tail", async () => {
    const { store, runner } = makeRunner();
    const job = store.create("https://media.example/fail-now", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("failed");
    expect(store.get(job.id)!.error).toContain("no video");
  });

  it("fails a job when the probe rejects (site down, bad URL)", async () => {
    const spawnImpl = fakeSpawn(goodYtdlp);
    const { store, runner } = makeRunner({
      probeImpl: async () => {
        throw new Error("probe failed: HTTP Error 404");
      },
      spawnImpl: spawnImpl as never,
    });
    const job = store.create("https://media.example/gone", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("failed");
    expect(store.get(job.id)!.error).toContain("404");
    expect(spawnImpl).not.toHaveBeenCalled();
  });

  it("runs at most the configured concurrency, then drains the queue", async () => {
    let live = 0;
    let peak = 0;
    const spawnImpl = fakeSpawn((args, proc) => {
      live += 1;
      peak = Math.max(peak, live);
      const outIdx = args.indexOf("-o") + 1;
      const file = args[outIdx].replace("%(ext)s", "mp4");
      setTimeout(() => {
        writeFileSync(file, "x");
        proc.stdout.emit("data", Buffer.from(`[download] Destination: ${file}\n`));
        proc.emit("close", 0);
        live -= 1;
      }, 15);
    });
    const { store, runner } = makeRunner({ concurrency: 2, spawnImpl: spawnImpl as never });
    const jobs = Array.from({ length: 5 }, (_, i) => store.create(`https://media.example/${i}`, "miles"));
    for (const j of jobs) runner.enqueue(j.id);
    for (const j of jobs) await settle(store, j.id);
    expect(peak).toBe(2);
    expect(jobs.every((j) => store.get(j.id)!.status === "done")).toBe(true);
  });

  it("a cancelled running job ends failed, not done", async () => {
    const { store, runner } = makeRunner();
    const job = store.create("https://media.example/hang", "miles");
    runner.enqueue(job.id);
    await new Promise((r) => setTimeout(r, 30));
    runner.cancel(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("failed");
    expect(store.get(job.id)!.error).toBe("cancelled");
  });

  it("a job is 'downloading' while its download runs, never stuck at probing", async () => {
    const { store, runner } = makeRunner();
    const job = store.create("https://media.example/hang", "miles");
    runner.enqueue(job.id);
    // Wait for the probe to pass and the spawn to be in flight.
    for (let i = 0; i < 100 && store.get(job.id)!.status !== "downloading"; i++) {
      await new Promise((r) => setTimeout(r, 5));
    }
    expect(store.get(job.id)!.status).toBe("downloading");
    runner.cancel(job.id);
    await settle(store, job.id);
  });

  it("a Destination line split across chunks still lands the file name", async () => {
    const spawnImpl = fakeSpawn((args, proc) => {
      const outIdx = args.indexOf("-o") + 1;
      const file = args[outIdx].replace("%(ext)s", "mp4");
      writeFileSync(file, "x");
      const line = `[download] Destination: ${file}\n`;
      const cut = Math.floor(line.length / 2);
      proc.stdout.emit("data", Buffer.from(line.slice(0, cut)));
      proc.stdout.emit("data", Buffer.from(line.slice(cut)));
      proc.emit("close", 0);
    });
    const { store, runner } = makeRunner({ spawnImpl: spawnImpl as never });
    const job = store.create("https://media.example/split", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("done");
    expect(store.get(job.id)!.fileName).toBe("download.mp4");
  });

  it("a delete during the probe stops the job before any download spawns", async () => {
    const spawnImpl = fakeSpawn(goodYtdlp);
    const { store, runner } = makeRunner({
      spawnImpl: spawnImpl as never,
      probeImpl: async () => {
        await new Promise((r) => setTimeout(r, 60));
        return { title: "late", extractor: "generic", fileSize: 10 };
      },
    });
    const job = store.create("https://media.example/slow-probe", "miles");
    runner.enqueue(job.id);
    await new Promise((r) => setTimeout(r, 20)); // probe in flight
    runner.cancel(job.id);
    await store.remove(job.id);
    await new Promise((r) => setTimeout(r, 80)); // probe resolves; cancel check fires
    expect(spawnImpl).not.toHaveBeenCalled();
    expect(store.get(job.id)).toBeNull();
  });

  it("a retry that lands while the run finishes is re-admitted, not stranded", async () => {
    let attempt = 0;
    const spawnImpl = fakeSpawn((args, proc) => {
      attempt += 1;
      if (attempt === 1) return; // first run hangs so we can retry mid-flight
      const outIdx = args.indexOf("-o") + 1;
      const file = args[outIdx].replace("%(ext)s", "mp4");
      writeFileSync(file, "x");
      proc.stdout.emit("data", Buffer.from(`[download] Destination: ${file}\n`));
      proc.emit("close", 0);
    });
    const { store, runner } = makeRunner({ spawnImpl: spawnImpl as never });
    const job = store.create("https://media.example/twice", "miles");
    runner.enqueue(job.id);
    await new Promise((r) => setTimeout(r, 30)); // first run is in flight
    // The retry path: mark queued and try to enqueue — refused (slot held)…
    store.update(job.id, { status: "queued" });
    runner.enqueue(job.id);
    runner.cancel(job.id); // …then the first run dies…
    await settle(store, job.id); // …and the queued mark is re-admitted, run again.
    expect(attempt).toBe(2);
    expect(store.get(job.id)!.status).toBe("done");
  });

  it("a final output line without a trailing newline still lands the file name", async () => {
    const spawnImpl = fakeSpawn((args, proc) => {
      const outIdx = args.indexOf("-o") + 1;
      const file = args[outIdx].replace("%(ext)s", "mp4");
      writeFileSync(file, "x");
      proc.stdout.emit("data", Buffer.from(`[download] Destination: ${file}`)); // no \n
      proc.emit("close", 0);
    });
    const { store, runner } = makeRunner({ spawnImpl: spawnImpl as never });
    const job = store.create("https://media.example/notail", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("done");
  });

  it("a delete landing inside the DNS lookup never starts the probe", async () => {
    const spawnImpl = fakeSpawn(goodYtdlp);
    const probeImpl = vi.fn(async () => ({ title: "x", extractor: "e", fileSize: 1 }));
    const { store, runner } = makeRunner({
      spawnImpl: spawnImpl as never,
      probeImpl,
      lookupImpl: async () => {
        await new Promise((r) => setTimeout(r, 60));
        return [{ address: "93.184.216.34" }];
      },
    });
    const job = store.create("https://media.example/dns-gap", "miles");
    runner.enqueue(job.id);
    await new Promise((r) => setTimeout(r, 20)); // DNS await in flight
    runner.cancel(job.id);
    await store.remove(job.id);
    await new Promise((r) => setTimeout(r, 80)); // lookup resolves; cancel check fires
    expect(probeImpl).not.toHaveBeenCalled();
    expect(spawnImpl).not.toHaveBeenCalled();
    expect(store.get(job.id)).toBeNull();
  });

  it("a failed registry write keeps the state and a later flush writes it", async () => {
    const { chmodSync, readFileSync } = await import("node:fs");
    const store = DownloadStore.load(dir);
    const job = store.create("https://media.example/fs-fail", "miles");
    chmodSync(dir, 0o555); // the disk is now broken for writes
    await expect(store.flush()).rejects.toThrow();
    expect(store.get(job.id)).not.toBeNull(); // memory kept, not reported clean
    chmodSync(dir, 0o755);
    store.update(job.id, { status: "failed", error: "x" });
    await store.flush();
    const onDisk = JSON.parse(readFileSync(path.join(dir, "jobs.json"), "utf-8"));
    expect(onDisk.jobs[0].status).toBe("failed");
  });

  it("a job whose DNS answers internal at run time fails refused, never spawned", async () => {
    const spawnImpl = fakeSpawn(goodYtdlp);
    const { store, runner } = makeRunner({
      spawnImpl: spawnImpl as never,
      lookupImpl: async () => [{ address: "10.9.9.9" }],
    });
    const job = store.create("https://sneaky.example/v", "miles");
    runner.enqueue(job.id);
    await settle(store, job.id);
    expect(store.get(job.id)!.status).toBe("failed");
    expect(store.get(job.id)!.error).toContain("refused");
    expect(spawnImpl).not.toHaveBeenCalled();
  });

  it("requeueStored resumes jobs left queued by a previous process", async () => {
    const { store } = makeRunner();
    const job = store.create("https://media.example/left", "miles");
    await store.flush();
    const { store: store2, runner: runner2 } = makeRunner();
    void store; // first store wrote the registry the second one reads
    runner2.requeueStored();
    await settle(store2, job.id);
    expect(store2.get(job.id)!.status).toBe("done");
  });
});
