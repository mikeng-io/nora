// Smoke test for the voice sidecar — the Node counterpart of
// deploy/stt/smoke_test.py. Needs no token and no container: it runs the
// real pump -> TCP -> frame-parser path (spike.mjs --mock) and asserts the
// numbers the DAVE spike measured.
//
//   node smoke_test.mjs
//
// Checks (mirroring the prototype's mock assertions):
//   1. the mock run exits 0 and both mock users are heard;
//   2. per-user frames/bytes match the synthesis (75 x 20 ms frames =
//      288000 bytes = 1.50 s of 48 kHz stereo s16le);
//   3. the silence ratio is sane for a 12k-amplitude sine (< 5%);
//   4. the framed fixture it leaves behind re-parses through consume.mjs
//      --file to the same per-user numbers.
//
// The child runs with its cwd in a fresh temp dir, so recordings/ artifacts
// land outside the repo tree.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PUMP_PORT = String(process.env.SMOKE_PUMP_PORT ?? 14599);

const EXPECTED = {
  'mock-user-1': { frames: 75, bytes: 288000 },
  'mock-user-2': { frames: 75, bytes: 288000 },
};

function parseStats(output) {
  // user <id>: bytes=<n> frames=<n> duration=<f>s silence=<f>%
  const users = {};
  for (const m of output.matchAll(
    /^user (\S+): bytes=(\d+) frames=(\d+) duration=([\d.]+)s silence=([\d.]+)%$/gm,
  )) {
    users[m[1]] = {
      bytes: Number(m[2]),
      frames: Number(m[3]),
      duration: Number(m[4]),
      silence: Number(m[5]),
    };
  }
  return users;
}

function assertStats(users, label) {
  for (const [id, want] of Object.entries(EXPECTED)) {
    const got = users[id];
    if (!got) throw new Error(`${label}: ${id} missing from stats`);
    if (got.frames !== want.frames) {
      throw new Error(`${label}: ${id} frames=${got.frames}, want ${want.frames}`);
    }
    if (got.bytes !== want.bytes) {
      throw new Error(`${label}: ${id} bytes=${got.bytes}, want ${want.bytes}`);
    }
    if (Math.abs(got.duration - 1.5) > 0.01) {
      throw new Error(`${label}: ${id} duration=${got.duration}s, want ~1.50s`);
    }
    if (got.silence > 5) {
      throw new Error(`${label}: ${id} silence=${got.silence}%, want < 5%`);
    }
  }
  console.log(`${label}: both users heard — 75 frames / 288000 bytes / ~1.50s each, silence < 5%`);
}

function main() {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), 'voice-sidecar-smoke-'));
  const env = { ...process.env, PUMP_PORT };

  // 1-3. the mock run through the real pump path
  const mockOut = execFileSync(process.execPath, [path.join(HERE, 'spike.mjs'), '--mock'], {
    cwd: workdir,
    env,
    encoding: 'utf8',
    timeout: 30_000,
  });
  process.stdout.write(mockOut);
  assertStats(parseStats(mockOut), 'mock run');

  // 4. the framed fixture re-parses to the same numbers
  const fixture = fs
    .readdirSync(path.join(workdir, 'recordings'))
    .find((f) => f.startsWith('mock-stream-') && f.endsWith('.bin'));
  if (!fixture) throw new Error('no framed fixture written by the mock run');
  const consumeOut = execFileSync(
    process.execPath,
    [path.join(HERE, 'consume.mjs'), '--file', path.join(workdir, 'recordings', fixture)],
    { env, encoding: 'utf8', timeout: 10_000 },
  );
  process.stdout.write(consumeOut);
  assertStats(parseStats(consumeOut), 'fixture re-parse');

  fs.rmSync(workdir, { recursive: true, force: true });
  console.log('SMOKE OK');
}

try {
  main();
} catch (err) {
  console.error(`SMOKE FAIL: ${err.message}`);
  process.exit(1);
}
