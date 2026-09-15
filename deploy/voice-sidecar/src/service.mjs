// Production entry for the audio-controller (the voice sidecar, phase 1 of
// design/voice-channel/07-audio-owner-controller.md).
//
//   node src/service.mjs
//
// This file is deliberately thin: it reads env, builds the REAL seams
// (discord.js join, @discordjs/voice player, ffmpeg spawn, fetch, timers),
// wires them through createCore, applies the boot decision, and serves the
// HTTP control plane. All logic lives in the injectable modules:
// controller.mjs (routes), lifecycle.mjs (connection/generation/desired
// state), arbiter.mjs (music/speech arbitration), outbox.mjs (events),
// state_store.mjs (durable desired-state), recv.js (wake PCM seam).
//
// The controller is the SOLE owner of the Discord voice connection; nora
// decides everything and drives join/exit/music/speech over HTTP.
//
// Health semantics: /health is LIVENESS, and it is deliberately 200 while
// the service is healthy-but-idle. A missing token is a configuration
// posture (voice not enabled on this deployment), not a process failure.
//
// Privacy posture: audio is transient. PCM is forwarded over the TCP seam
// and music flows CDN -> ffmpeg -> Discord; nothing is written to disk.

import { spawn } from 'node:child_process';
import { Readable } from 'node:stream';
import {
  createAudioPlayer,
  createAudioResource,
  entersState,
  NoSubscriberBehavior,
  StreamType,
} from '@discordjs/voice';

import { createCore } from './controller.mjs';
import { bootChannel } from './lifecycle.mjs';
import { realClock } from './outbox.mjs';
import { join, createPump } from './recv.js';
import { createStateStore } from './state_store.mjs';

const HOST = process.env.HOST ?? '127.0.0.1';
const PORT = Number(process.env.PORT ?? 4600);
const PUMP_HOST = process.env.PUMP_HOST ?? '127.0.0.1';
const PUMP_PORT = Number(process.env.PUMP_PORT ?? 4599);
const NORA_EVENT_URL = process.env.NORA_EVENT_URL ?? 'http://nora:9100/app/v1/voice/event';
const STATE_FILE = process.env.STATE_FILE ?? '/var/lib/audio-controller/desired_state.json';

const TOKEN = process.env.DISCORD_BOT_TOKEN;
const GUILD_ID = process.env.GUILD_ID;
const CHANNEL_ID = process.env.CHANNEL_ID;

function log(event, fields = {}) {
  const line = { event, ts: new Date().toISOString(), guild_id: GUILD_ID ?? null, ...fields };
  const out = `[voc] ${JSON.stringify(line)}`;
  if (event === 'error' || event === 'stream-failed' || event === 'event_delivery_failed') {
    console.error(out);
  } else {
    console.log(out);
  }
}

const perUser = new Map(); // userId -> {frames, bytes} since process start
let currentPump = null;

// The real joinVoice seam: pump + discord.js join + AudioPlayer, one per
// connection. The wake TCP framing is unchanged from the receive sidecar.
async function joinVoice(channelId) {
  const pump = createPump({ host: PUMP_HOST, port: PUMP_PORT });
  currentPump = pump;
  const joined = await join(GUILD_ID, channelId, TOKEN);
  const player = createAudioPlayer({
    behaviors: { noSubscriber: NoSubscriberBehavior.Pause },
  });
  joined.connection.subscribe(player);

  joined.bus.on('listening', (userId) => {
    log('listening', { user_id: userId, channel_id: channelId });
  });
  joined.bus.on('pcm', (userId, pcm) => {
    const s = perUser.get(userId) ?? { frames: 0, bytes: 0 };
    s.frames += 1;
    s.bytes += pcm.length;
    perUser.set(userId, s);
    pump(pcm, userId);
  });
  joined.bus.on('streamError', (userId, err) => {
    log('error', { event: 'receive_stream_error', user_id: userId, error: String(err?.message ?? err) });
  });

  return { ...joined, pump, player };
}

const stateStore = createStateStore({ path: STATE_FILE });

const core = createCore({
  guildId: GUILD_ID,
  joinVoice,
  entersStateFn: (connection, status, timeoutMs) => entersState(connection, status, timeoutMs),
  stateStore,
  spawn: (args) => spawn('ffmpeg', args, { stdio: ['ignore', 'pipe', 'inherit'] }),
  createMusicResource: (stream) =>
    createAudioResource(stream, { inputType: StreamType.Raw, inlineVolume: true }),
  createSpeechResource: (audio) =>
    // Arbitrary: the demuxer probes mp3/wav itself (ffmpeg is in the image).
    createAudioResource(Readable.from([audio]), { inputType: StreamType.Arbitrary }),
  fetchImpl: (...args) => fetch(...args),
  eventUrl: NORA_EVENT_URL,
  clock: realClock,
  getWakeStats: () => {
    const users = {};
    for (const [userId, s] of perUser) users[userId] = s;
    return users;
  },
  pumpInfo: () => ({
    host: PUMP_HOST,
    port: PUMP_PORT,
    ...(currentPump ? { drops: currentPump.stats() } : {}),
  }),
  log,
});

core.server.on('error', (err) => {
  // The health endpoint IS the liveness contract (HEALTHCHECK curls it); if
  // it cannot bind, liveness is genuinely unavailable — die with a clear
  // log line and let the container's restart policy retry.
  console.error(`[voc] ${JSON.stringify({ event: 'error', error: `health endpoint failed on ${HOST}:${PORT}: ${err.message}` })}`);
  process.exit(1);
});
core.server.listen(PORT, HOST, () => {
  log('listening_http', { host: HOST, port: PORT });
});

// Boot decision (§12.1): join ONLY if the desired-state file says
// connected, OR the file is absent AND env CHANNEL_ID is set (first boot).
// A file saying `idle` wins over CHANNEL_ID.
if (TOKEN && GUILD_ID) {
  const desired = await stateStore.read();
  const channel = bootChannel(desired, CHANNEL_ID);
  if (channel) {
    core.lifecycle.join(channel).catch(() => {
      // Join failed: state is `error`, /health stays live, compose's
      // restart policy gives the retry. Already logged by the lifecycle.
    });
  } else {
    log('boot_idle', { reason: desired?.desired === 'idle' ? 'desired_state_idle' : 'no_channel' });
  }
} else {
  const missing = [
    ['DISCORD_BOT_TOKEN', TOKEN],
    ['GUILD_ID', GUILD_ID],
  ]
    .filter(([, v]) => !v)
    .map(([k]) => k)
    .join(', ');
  log('boot_idle', { reason: 'healthy-but-idle', missing });
}

async function shutdown(signal) {
  log('shutdown', { signal });
  await core.lifecycle.shutdown();
  core.server.close(() => process.exit(0));
  // Don't hang on a half-open voice socket.
  setTimeout(() => process.exit(0), 3000).unref();
}
process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
