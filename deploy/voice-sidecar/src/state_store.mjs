// Durable desired-state (design/voice-channel/07-audio-owner-controller.md
// §12.1): a tiny JSON file on a local volume recording whether the
// controller WANTS to be connected. Writes are atomic (tmp + rename) so a
// crash mid-write never leaves a half file.
//
// read() NEVER collapses distinct failure shapes into one: a missing file is
// 'absent' (first boot — the boot decision may apply env CHANNEL_ID), but an
// unreadable or corrupt file is 'error'/'corrupt' and must be surfaced
// (fail-closed: NOT a first boot — silently first-booting could rejoin a
// channel the user just exited).

import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

export function createStateStore({ path: filePath }) {
  async function read() {
    let raw;
    try {
      raw = await readFile(filePath, 'utf8');
    } catch (err) {
      if (err?.code === 'ENOENT') return { status: 'absent', state: null };
      return { status: 'error', state: null, error: String(err?.message ?? err) };
    }
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') return { status: 'ok', state: parsed };
      return { status: 'corrupt', state: null, error: 'state file is not a JSON object' };
    } catch (err) {
      return { status: 'corrupt', state: null, error: String(err?.message ?? err) };
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

// Which of a state directory's files name a guild, and which do not.
// Lives HERE, not in service.mjs, so it can be TESTED: service.mjs imports
// discord.js at module scope, and a falsification probe that re-adopted junk
// filenames reddened nothing because nothing could import it. Same trap the
// codec and the pump were in.
//
// Only a Discord snowflake names a guild's state. Before multi-guild this
// directory held a single `desired_state.json`, and globbing `*.json` adopted
// it as a guild literally called "desired_state" — every boot tried
// `guilds.fetch("desired_state")`, failed, and left a junk core in the map
// and in /health, while the REAL guild's connection was never restored.
//
// Skipped rather than migrated: the old file records a channel for a guild it
// does not name, so there is nothing to migrate it to.
export function guildsFromStateFiles(files) {
  const guilds = [];
  const skipped = [];
  for (const file of files) {
    if (!file.endsWith('.json')) continue;
    const guildId = file.slice(0, -'.json'.length);
    if (/^[0-9]{15,25}$/.test(guildId)) guilds.push(guildId);
    else skipped.push(file);
  }
  return { guilds, skipped };
}
