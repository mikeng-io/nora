// Durable desired-state (design/voice-channel/07-audio-owner-controller.md
// §12.1): a tiny JSON file on a local volume recording whether the
// controller WANTS to be connected. Writes are atomic (tmp + rename) so a
// crash mid-write never leaves a half file. A missing or unreadable file
// reads as null ("absent") — the boot decision treats absent as
// first-boot.

import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

export function createStateStore({ path: filePath }) {
  async function read() {
    try {
      const parsed = JSON.parse(await readFile(filePath, 'utf8'));
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch {
      // ENOENT (first boot) and a corrupt file both read as "absent".
      return null;
    }
  }

  async function write(state) {
    await mkdir(path.dirname(filePath), { recursive: true });
    const tmp = `${filePath}.${process.pid}.tmp`;
    await writeFile(tmp, JSON.stringify(state));
    await rename(tmp, filePath);
  }

  return { read, write, path: filePath };
}
