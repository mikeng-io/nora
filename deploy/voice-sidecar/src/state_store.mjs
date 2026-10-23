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

import { constants, realpathSync } from 'node:fs';
import { mkdir, open, rename, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

export function createStateStore({ path: filePath }) {
  async function read() {
    let raw;
    try {
      // O_NOFOLLOW: a symlink planted at the state file's own name is an error, not a read
      // of wherever it points (containedFile checks the name once; this holds at the read).
      const handle = await open(filePath, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
      try {
        raw = await handle.readFile('utf8');
      } finally {
        await handle.close();
      }
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
    // 'wx' (O_EXCL) never follows a link at the temp name; a leftover or planted entry there
    // is removed (unlink drops the link itself, not its target) and the create retried once.
    try {
      await writeFile(tmp, JSON.stringify(state), { flag: 'wx' });
    } catch (err) {
      if (err?.code !== 'EEXIST') throw err;
      await unlink(tmp);
      await writeFile(tmp, JSON.stringify(state), { flag: 'wx' });
    }
    await rename(tmp, filePath);
  }

  return { read, write, path: filePath };
}

// The path of `name` inside `dir`, and only that: a guild id reaches here from
// the control plane (`guild_id` in a request body), so a name with a separator,
// a `..` or an absolute form is refused, and so is a file whose real location
// (a symlink planted under the name) lies outside `dir`. Checked on the
// resolved paths; the returned path is the plain join the caller expects.
const UNSAFE_NAME = /[\\/]/;

function realOrSelf(p) {
  try {
    return realpathSync(p);
  } catch {
    return p;
  }
}

export function containedFile(dir, name) {
  if (typeof name !== 'string' || name === '' || name === '.' || name === '..' || UNSAFE_NAME.test(name)) {
    throw new Error(`refusing state file name ${JSON.stringify(name)}`);
  }
  const realBase = realOrSelf(path.resolve(dir));
  const real = realOrSelf(path.join(realBase, name));
  const rel = path.relative(realBase, real);
  if (rel === '' || rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
    throw new Error(`state file ${JSON.stringify(name)} escapes its directory`);
  }
  return path.join(dir, name);
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
