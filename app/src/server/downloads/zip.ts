import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { crc32 } from "node:zlib";

// A zip archive written by hand. STORE entries only — media is already
// compressed, so deflate buys nothing — and data descriptors so each file
// streams through once, no pre-pass over its bytes. `zip` is not on the
// node:slim image and a dependency for one endpoint is not worth it.
// # ponytail: classic zip — an entry or archive at 4 GiB+ needs ZIP64, and
// callers refuse before that rather than writing one.

const SIG_LFH = 0x04034b50;
const SIG_DD = 0x08074b50;
const SIG_CDH = 0x02014b50;
const SIG_EOCD = 0x06054b50;
// utf-8 entry names | crc+sizes trail in a data descriptor.
const FLAGS = 0x0808;

export const ZIP32_LIMIT = 0xffffffff;

export interface PlannedEntry {
  /** Archive path, UTF-8 bytes — length is counted in bytes below. */
  name: Buffer;
  path: string;
  size: number;
  dosTime: number;
  dosDate: number;
}

function dosDateTime(d: Date): { dosTime: number; dosDate: number } {
  return {
    dosTime: (d.getHours() << 11) | (d.getMinutes() << 5) | Math.floor(d.getSeconds() / 2),
    dosDate: ((Math.max(1980, d.getFullYear()) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}

/** Stat an entry into a plan; a file swept between the listing and now is
 * skipped rather than failing the archive. */
export async function planEntry(name: string, path: string): Promise<PlannedEntry | null> {
  try {
    const st = await stat(path);
    if (!st.isFile()) return null;
    return { name: Buffer.from(name, "utf-8"), path, size: st.size, ...dosDateTime(st.mtime) };
  } catch {
    return null;
  }
}

/** The exact byte count the stream will emit — the archive is one buffer
 * short of fully declared, so no content-length is set anyway; this exists
 * for the 4 GiB refuse check. */
export function zipBytes(plans: PlannedEntry[]): number {
  return (
    plans.reduce((n, e) => n + e.size + 30 + 16 + 46 + e.name.length * 2, 0) + 22
  );
}

/** The archive as a byte stream: local headers, each file's bytes, a data
 * descriptor, then the central directory and end record. */
export async function* streamZip(plans: PlannedEntry[]): AsyncGenerator<Buffer> {
  let offset = 0;
  const done: { e: PlannedEntry; offset: number; crc: number; size: number }[] = [];
  for (const e of plans) {
    const lfh = Buffer.alloc(30);
    lfh.writeUInt32LE(SIG_LFH, 0);
    lfh.writeUInt16LE(20, 4); // version needed
    lfh.writeUInt16LE(FLAGS, 6);
    lfh.writeUInt16LE(0, 8); // STORE
    lfh.writeUInt16LE(e.dosTime, 10);
    lfh.writeUInt16LE(e.dosDate, 12);
    lfh.writeUInt16LE(e.name.length, 26);
    yield Buffer.concat([lfh, e.name]);
    const entryOffset = offset;
    offset += 30 + e.name.length;

    let crc = 0;
    let sent = 0;
    for await (const chunk of createReadStream(e.path)) {
      crc = crc32(chunk, crc);
      sent += chunk.length;
      yield chunk;
    }
    const dd = Buffer.alloc(16);
    dd.writeUInt32LE(SIG_DD, 0);
    dd.writeUInt32LE(crc, 4);
    dd.writeUInt32LE(sent, 8);
    dd.writeUInt32LE(sent, 12);
    yield dd;
    offset += sent + 16;
    done.push({ e, offset: entryOffset, crc, size: sent });
  }

  const cdStart = offset;
  for (const d of done) {
    const cd = Buffer.alloc(46);
    cd.writeUInt32LE(SIG_CDH, 0);
    cd.writeUInt16LE(20, 4); // version made by
    cd.writeUInt16LE(20, 6); // version needed
    cd.writeUInt16LE(FLAGS, 8);
    cd.writeUInt16LE(0, 10);
    cd.writeUInt16LE(d.e.dosTime, 12);
    cd.writeUInt16LE(d.e.dosDate, 14);
    cd.writeUInt32LE(d.crc, 16);
    cd.writeUInt32LE(d.size, 20);
    cd.writeUInt32LE(d.size, 24);
    cd.writeUInt16LE(d.e.name.length, 28);
    cd.writeUInt32LE(d.offset, 42);
    yield Buffer.concat([cd, d.e.name]);
    offset += 46 + d.e.name.length;
  }
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(SIG_EOCD, 0);
  eocd.writeUInt16LE(done.length, 8);
  eocd.writeUInt16LE(done.length, 10);
  eocd.writeUInt32LE(offset - cdStart, 12);
  eocd.writeUInt32LE(cdStart, 16);
  yield eocd;
}
