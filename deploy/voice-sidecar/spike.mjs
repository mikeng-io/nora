// Spike harness for the DAVE receive path — verification tooling, NOT the
// production entry (that is src/service.mjs).
//
//   node spike.mjs --mock              no token needed: synthesizes PCM for
//                                      two fake users, runs it through the
//                                      real pump -> TCP -> frame-parser path
//                                      (plus WAV output), prints stats.
//   node spike.mjs --live              operator-run live spike. Env:
//                                        DISCORD_BOT_TOKEN (required)
//                                        GUILD_ID, CHANNEL_ID (required)
//                                        PUMP=1 PUMP_PORT=4599 (optional:
//                                        also forward PCM to the wake-gate
//                                        seam, e.g. consume.mjs)
//
// Live mode subscribes to every speaking member, writes
// recordings/<userId>-<ts>.wav per user, and prints a "listening" line per
// user as streams attach. WAV writing is a spike-verification artifact only
// (see src/wav.js); the production service never records.

import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { join, createPump, createFrameParser, PCM_FRAME_BYTES } from './src/recv.js';
import { WavWriter } from './src/wav.js';

const RECORDINGS = path.resolve('recordings');
const PUMP_PORT = Number(process.env.PUMP_PORT ?? 4599);

function stats() {
  // per-user counters shared by mock and live paths
  const perUser = new Map();
  return {
    note(userId, pcm) {
      const s = perUser.get(userId) ?? { bytes: 0, frames: 0, silentSamples: 0, totalSamples: 0 };
      s.bytes += pcm.length;
      s.frames += Math.round(pcm.length / PCM_FRAME_BYTES) || 1;
      for (let i = 0; i + 1 < pcm.length; i += 2) {
        const v = pcm.readInt16LE(i);
        s.totalSamples++;
        if (Math.abs(v) < 100) s.silentSamples++;
      }
      perUser.set(userId, s);
    },
    report() {
      const lines = [];
      for (const [userId, s] of perUser) {
        const silence = s.totalSamples ? (s.silentSamples / s.totalSamples) : 0;
        const seconds = s.bytes / 4 / 48000; // s16le stereo 48k
        lines.push(
          `user ${userId}: bytes=${s.bytes} frames=${s.frames} ` +
          `duration=${seconds.toFixed(2)}s silence=${(silence * 100).toFixed(1)}%`,
        );
      }
      return lines.join('\n');
    },
  };
}

// --- mock -----------------------------------------------------------------

function synthFrame(freq, amp) {
  const frame = Buffer.alloc(PCM_FRAME_BYTES);
  for (let i = 0; i < PCM_FRAME_BYTES / 4; i++) {
    const t = i / 48000;
    const v = Math.round(amp * Math.sin(2 * Math.PI * freq * t));
    frame.writeInt16LE(v, i * 4);
    frame.writeInt16LE(v, i * 4 + 2);
  }
  return frame;
}

async function mock() {
  fs.mkdirSync(RECORDINGS, { recursive: true });
  const ts = Date.now();
  const st = stats();
  const wavs = new Map();

  // Real TCP listener on the other end of the pump — same code path as the
  // future wake gate — plus per-user WAV writers. The raw framed stream is
  // also dumped to a fixture so `consume.mjs --file` can re-verify offline.
  const fixturePath = path.join(RECORDINGS, `mock-stream-${ts}.bin`);
  const fixture = fs.createWriteStream(fixturePath);
  const server = net.createServer((conn) => {
    conn.on('data', (chunk) => fixture.write(chunk));
    const feed = createFrameParser((userId, pcm) => {
      st.note(userId, pcm);
      let w = wavs.get(userId);
      if (!w) {
        const file = path.join(RECORDINGS, `${userId}-${ts}.wav`);
        w = new WavWriter(file);
        wavs.set(userId, w);
        console.log(`[mock] listening: user ${userId} -> ${file}`);
      }
      w.write(pcm);
    });
    conn.on('data', feed);
  });
  await new Promise((r) => server.listen(PUMP_PORT, '127.0.0.1', r));

  const pump = createPump({ guildId: '100000000000000203', token: 'synthetic-smoke-token', port: PUMP_PORT });
  // Two fake users: 1.5 s tone each (75 x 20ms frames), interleaved, with a
  // different pitch per user so the WAVs are audibly distinct.
  const users = [
    { id: 'mock-user-1', freq: 220 },
    { id: 'mock-user-2', freq: 330 },
  ];
  // pump() drops frames until the socket connects; give it a tick first.
  await new Promise((r) => setTimeout(r, 200));
  for (let i = 0; i < 75; i++) {
    for (const u of users) pump(synthFrame(u.freq, 12000), u.id);
    await new Promise((r) => setTimeout(r, 5));
  }
  await new Promise((r) => setTimeout(r, 300));

  pump.close();
  server.close();
  for (const w of wavs.values()) w.close();
  fixture.end();
  console.log('[mock] pump path complete. Stats:');
  console.log(st.report());
  console.log(`[mock] framed fixture: ${fixturePath} (verify with: node consume.mjs --file ${fixturePath})`);
  if (!wavs.size) {
    console.error('[mock] FAIL: no frames reached the listener');
    process.exitCode = 1;
  }
}

// --- live -----------------------------------------------------------------

async function live() {
  const token = process.env.DISCORD_BOT_TOKEN;
  const guildId = process.env.GUILD_ID;
  const channelId = process.env.CHANNEL_ID;
  if (!token || !guildId || !channelId) {
    console.error('live mode needs DISCORD_BOT_TOKEN, GUILD_ID, CHANNEL_ID in env');
    process.exit(2);
  }
  fs.mkdirSync(RECORDINGS, { recursive: true });
  const ts = Date.now();
  const st = stats();
  const wavs = new Map();
  const doPump = process.env.PUMP === '1';
  const pump = doPump ? createPump({ guildId, token: process.env.NORA_BRIDGE_TOKEN, port: PUMP_PORT }) : null;

  const { bus, leave } = await join(guildId, channelId, token);
  console.log(`[live] joined channel ${channelId}; waiting for speakers (Ctrl-C to stop)`);

  bus.on('listening', (userId) => {
    const file = path.join(RECORDINGS, `${userId}-${ts}.wav`);
    wavs.set(userId, new WavWriter(file));
    console.log(`[live] listening: user ${userId} -> ${file}`);
  });
  bus.on('pcm', (userId, pcm) => {
    st.note(userId, pcm);
    wavs.get(userId)?.write(pcm);
    pump?.(pcm, userId);
  });
  bus.on('streamError', (userId, err) => {
    console.error(`[live] stream error for user ${userId}: ${err.message} (will resubscribe on next speech)`);
  });

  process.on('SIGINT', async () => {
    for (const w of wavs.values()) w.close();
    pump?.close();
    await leave();
    console.log('\n[live] stopped. Stats:');
    console.log(st.report());
    process.exit(0);
  });
}

const mode = process.argv[2];
if (mode === '--mock') await mock();
else if (mode === '--live') await live();
else {
  console.error('usage: node spike.mjs --mock | --live');
  process.exit(2);
}
