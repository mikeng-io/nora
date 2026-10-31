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

import { spawn, execFileSync } from 'node:child_process';
import { Readable } from 'node:stream';
import { pathToFileURL } from 'node:url';
import {
  createAudioPlayer,
  createAudioResource,
  entersState,
  NoSubscriberBehavior,
  StreamType,
} from '@discordjs/voice';

import { readdir } from 'node:fs/promises';
import { appendFileSync, renameSync, statSync } from 'node:fs';
import path from 'node:path';

import { buildCore, createCoreRegistry, createFrontDoor } from './controller.mjs';
import { bootChannel } from './lifecycle.mjs';
import { realClock } from './outbox.mjs';
import { join, createPump } from './recv.js';
import { containedFile, createStateStore, guildsFromStateFiles } from './state_store.mjs';

const HOST = process.env.HOST ?? '127.0.0.1';
const PORT = Number(process.env.PORT ?? 4600);
const PUMP_HOST = process.env.PUMP_HOST ?? '127.0.0.1';
const PUMP_PORT = Number(process.env.PUMP_PORT ?? 4599);
// In bridge mode the discord-bridge owns the VoiceEventConsumer
// (/v1/voice/event, bearer-authed like every /v1 route there) — the app
// relinquished it, so the shipped default targets the bridge.
const NORA_EVENT_URL = process.env.NORA_EVENT_URL ?? 'http://discord-bridge:18093/v1/voice/event';
// A DIRECTORY now, one `<guild_id>.json` inside it (multi-guild 2026-09-17).
// Configured guilds come from Nora's existing [speech.tts].sidecars list,
// mounted read-only at /app/config.toml. State records desired connections,
// but cannot grant admission to a guild removed from that configuration.
const STATE_DIR = process.env.STATE_DIR ?? '/var/lib/audio-controller';

// The Discord bot credential is separate from the shared service bearer.
const TOKEN = process.env.DISCORD_BOT_TOKEN;
// The shared service bearer (NORA_BRIDGE_TOKEN), the same value nora and the
// bridges hold. Required: it guards every route here and the wake-gate seam
// hello, so a process without it refuses to start rather than serve open.
const BRIDGE_TOKEN = process.env.NORA_BRIDGE_TOKEN || '';

// Mirror of the log into the state VOLUME, which survives what stdout does
// not. `docker logs` keeps nothing across `up -d --force-recreate`, and that
// is the command every deploy runs — so the loop has been: music breaks,
// restart to restore service, the evidence is destroyed by the act of
// restoring it, the mechanism is guessed at. Four rounds of that, and the
// one line that would have named the cause (ffmpeg's stderr tail, which
// crosses to nora in NO event) died with the container every time.
//
// `/var/lib/audio-controller` is a named volume and already survives both
// recreate and image rebuild — the desired-state files there predate them.
const LOG_PATH = path.join(STATE_DIR, 'voc.log');
const LOG_MAX_BYTES = 16 * 1024 * 1024;
let logSize = -1; // -1 = not yet known; a failed stat disables the mirror

function mirrorLog(text) {
  if (logSize === null) return; // disabled after a failure — never retried per line
  try {
    if (logSize < 0) {
      try {
        logSize = statSync(LOG_PATH).size;
      } catch {
        logSize = 0; // absent is fine: the first append creates it
      }
    }
    if (logSize > LOG_MAX_BYTES) {
      // One generation back, not a ring: a wedge is diagnosed from the hours
      // around it, and two files bound the disk without hiding them.
      try {
        renameSync(LOG_PATH, `${LOG_PATH}.1`);
      } catch {
        /* keep appending rather than lose the line */
      }
      logSize = 0;
    }
    appendFileSync(LOG_PATH, text + '\n');
    logSize += text.length + 1;
  } catch {
    // Logging must never be able to stop playback. One failure disables the
    // mirror for the life of the process; stdout is unaffected.
    logSize = null;
  }
}

function log(event, fields = {}) {
  const line = { event, ts: new Date().toISOString(), ...fields };
  const out = `[voc] ${JSON.stringify(line)}`;
  mirrorLog(JSON.stringify(line));
  if (event === 'error' || event === 'stream-failed' || event === 'event_delivery_failed') {
    console.error(out);
  } else {
    console.log(out);
  }
}

// The real joinVoice seam: pump + discord.js join + AudioPlayer, one per
// connection. The wake TCP framing is unchanged from the receive sidecar.
//
// Pump ownership (2026-09-16 audit N-5/CN-2): every failed construction
// path closes what it built — the fresh pump's TCP socket and whatever the
// join already assembled — and the PREVIOUS pump is closed only once its
// replacement is fully joined, so a failed replacement never kills a
// working pump. Exported with injected seams so these rules are testable;
// main() passes the real implementations.
export function createJoinVoice({ joinImpl, createPumpImpl, createPlayerImpl, log: logFn }) {
  const perUser = new Map(); // userId -> {frames, bytes} since process start
  let currentPump = null;

  async function joinVoice(channelId) {
    const pump = createPumpImpl();
    let joined = null;
    try {
      joined = await joinImpl(channelId);
      const player = createPlayerImpl();
      // `subscribe()` returns undefined instead of throwing when the
      // connection can't take a subscriber (e.g. already destroyed) — with
      // NoSubscriberBehavior.Pause that failure was previously invisible:
      // ffmpeg runs, decodes, never stalls, and the player just sits
      // AutoPaused with nothing reaching Discord (measured 2026-09-20,
      // guild 100000000000000201 — one `play` log, no error, no sound).
      const subscription = joined.connection.subscribe(player);
      if (!subscription) {
        logFn('error', { event: 'subscribe_failed', channel_id: channelId });
      } else {
        logFn('subscribe', { ok: true, channel_id: channelId });
      }

      joined.bus.on('listening', (userId) => {
        logFn('listening', { user_id: userId, channel_id: channelId });
      });
      joined.bus.on('pcm', (userId, pcm) => {
        const s = perUser.get(userId) ?? { frames: 0, bytes: 0 };
        s.frames += 1;
        s.bytes += pcm.length;
        perUser.set(userId, s);
        pump(pcm, userId);
      });
      joined.bus.on('streamError', (userId, err) => {
        logFn('error', { event: 'receive_stream_error', user_id: userId, error: String(err?.message ?? err) });
      });

      // The replacement is fully joined: NOW the old pump is superseded.
      if (currentPump) {
        try {
          currentPump.close();
        } catch {
          /* already closed */
        }
      }
      currentPump = pump;
      return { ...joined, pump, player };
    } catch (err) {
      try {
        pump.close();
      } catch {
        /* never connected */
      }
      if (joined?.leave) {
        try {
          await joined.leave();
        } catch {
          /* half-open socket */
        }
      }
      throw err;
    }
  }

  return { joinVoice, perUser, currentPump: () => currentPump };
}

// python3 is already installed in the sidecar image; use its stdlib TOML
// parser rather than interpreting TOML a second way or adding a dependency.
export function loadConfiguredGuilds(configPath = '/app/config.toml') {
  try {
    const result = execFileSync('python3', ['-c', `
import json, sys, tomllib
with open(sys.argv[1], 'rb') as stream:
    entries = tomllib.load(stream).get('speech', {}).get('tts', {}).get('sidecars', [])
guilds = [entry['guild'] for entry in entries]
if any(not isinstance(guild, str) or not guild.isascii() or not guild.isdecimal()
       or not 17 <= len(guild) <= 20 for guild in guilds):
    raise ValueError('invalid guild')
print(json.dumps(guilds))
`, configPath], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    return new Set(JSON.parse(result));
  } catch {
    // Neither TOML values nor parser/subprocess diagnostics may reach logs.
    throw new Error('Cannot load configured voice guilds; refusing to start');
  }
}

async function main() {
  const configuredGuilds = loadConfiguredGuilds();
  // ONE CORE PER GUILD, in one process (multi-guild, 2026-09-17). Each is the
  // same single-connection state machine §12.5 describes — a generation
  // counter, an outbox, a desired-state file — instantiated per guild rather
  // than keyed by one. Nothing is shared between them but the bot token and
  // the socket out front.
  //
  // The map itself, and the mint-vs-reuse-vs-prune decision every lookup
  // makes, live in controller.mjs's createCoreRegistry (#345); the
  // forward-ref + `onJoinFailed` wiring THE FIX depends on lives in
  // controller.mjs's `buildCore` (shared with test/core_registry.test.mjs's
  // end-to-end test, so a break here — dropping this call, or the registry/
  // guildId it's given — cannot pass silently: that test drives this exact
  // function, not a copy of its wiring). This file only supplies the
  // discord.js-dependent seams `buildCore` needs to build one core.
  function makeCore(guildId) {
    // Each guild gets its OWN pump, so its own wake-seam connection: the
    // hello frame on that connection is what tells the gate which guild the
    // audio is from, and one pump serving two guilds could not say.
    const { joinVoice, perUser, currentPump } = createJoinVoice({
      joinImpl: (channelId) => join(guildId, channelId, TOKEN),
      createPumpImpl: () => createPump({ guildId, token: BRIDGE_TOKEN, host: PUMP_HOST, port: PUMP_PORT }),
      createPlayerImpl: () =>
        createAudioPlayer({
          behaviors: { noSubscriber: NoSubscriberBehavior.Pause },
        }),
      log: (event, fields = {}) => log(event, { guild_id: guildId, ...fields }),
    });

    return buildCore(registry, guildId, {
      joinVoice,
      entersStateFn: (connection, status, timeoutMs) => entersState(connection, status, timeoutMs),
      stateStore: createStateStore({ path: containedFile(STATE_DIR, `${guildId}.json`) }),
      spawn: (args) => spawn('ffmpeg', args, { stdio: ['ignore', 'pipe', 'pipe'] }),
      createMusicResource: (stream) =>
        createAudioResource(stream, { inputType: StreamType.Raw, inlineVolume: true }),
      createSpeechResource: (audio) =>
        // Arbitrary: the demuxer probes mp3/wav itself (ffmpeg is in the image).
        createAudioResource(Readable.from([audio]), { inputType: StreamType.Arbitrary }),
      fetchImpl: (...args) => fetch(...args),
      eventUrl: NORA_EVENT_URL,
      eventHeaders: { authorization: `Bearer ${BRIDGE_TOKEN}` },
      clock: realClock,
      getWakeStats: () => {
        const users = {};
        for (const [userId, st] of perUser) users[userId] = st;
        return users;
      },
      pumpInfo: () => ({
        host: PUMP_HOST,
        port: PUMP_PORT,
        ...(currentPump() ? { drops: currentPump().stats() } : {}),
      }),
      log: (event, fields = {}) => log(event, { guild_id: guildId, ...fields }),
    });
  }

  // Without a token there is nothing to join with, so nothing is created —
  // same posture as before this was `!TOKEN` inline in `controllerFor`.
  const registry = createCoreRegistry({ makeCore, canCreate: () => Boolean(TOKEN), log });

  const server = createFrontDoor({
    authToken: BRIDGE_TOKEN,
    // Configuration is the admission boundary, including for existing cores.
    // New cores also require Discord bot membership; generation is replay control.
    authorizeGuild: async (guildId) => {
      if (!configuredGuilds.has(guildId)) return false;
      if (registry.guildIds().includes(guildId)) return true;
      if (!TOKEN) return false;
      const response = await fetch(`https://discord.com/api/v10/guilds/${guildId}`, {
        headers: { Authorization: `Bot ${TOKEN}` },
        signal: AbortSignal.timeout(5000),
      });
      await response.body?.cancel();
      return response.ok;
    },
    controllerFor: registry.controllerFor,
    // Process-level liveness, not a guild's. The compose HEALTHCHECK curls
    // this and it must answer 200 while idle with no guild at all.
    health: () => ({
      status: 'ok',
      guilds: registry.guildIds(),
      connections: registry.entries().map(([guildId, c]) => ({
        guild_id: guildId,
        voice: c.lifecycle.voice(),
        channel_id: c.lifecycle.channelId(),
      })),
    }),
    log,
  });

  server.on('error', (err) => {
    // The health endpoint IS the liveness contract (HEALTHCHECK curls it); if
    // it cannot bind, liveness is genuinely unavailable — die with a clear
    // log line and let the container's restart policy retry.
    console.error(`[voc] ${JSON.stringify({ event: 'error', error: `health endpoint failed on ${HOST}:${PORT}: ${err.message}` })}`);
    process.exit(1);
  });
  server.listen(PORT, HOST, () => {
    log('listening_http', { host: HOST, port: PORT });
  });

  // Boot (§12.1): restore whatever the STATE DIRECTORY says was connected.
  // Restore only guilds still present in the shared configuration.
  //
  // A guild whose file says `idle` is NOT restored and NOT created: a restart
  // must not rejoin a channel someone just left. A file that cannot be read
  // is not a first boot either — fail closed, log loudly, stay out.
  if (TOKEN) {
    let files = [];
    try {
      files = (await readdir(STATE_DIR)).filter((f) => f.endsWith('.json'));
    } catch (err) {
      if (err?.code !== 'ENOENT') {
        log('error', { event: 'state_dir_read_failed', path: STATE_DIR, error: String(err?.message ?? err) });
      }
    }
    const { guilds, skipped } = guildsFromStateFiles(files);
    for (const file of skipped) {
      log('boot_skip_state_file', {
        file,
        reason: 'not a guild snowflake (pre-multi-guild state file or junk)',
      });
    }
    for (const guildId of guilds) {
      if (!configuredGuilds.has(guildId)) continue;
      const file = `${guildId}.json`;
      const store = createStateStore({ path: containedFile(STATE_DIR, file) });
      const stored = await store.read();
      if (stored.status === 'corrupt' || stored.status === 'error') {
        log('error', {
          event: 'state_store_read_failed',
          guild_id: guildId,
          status: stored.status,
          path: store.path,
          error: stored.error ?? null,
        });
        continue;
      }
      // No env fallback channel any more: `bootChannel`'s second argument was
      // CHANNEL_ID, and the only honest first-boot posture for a process that
      // serves N guilds is idle until someone joins.
      const channel = bootChannel(stored.state, null, log);
      if (!channel) {
        log('boot_idle', { guild_id: guildId, reason: stored.state?.desired === 'idle' ? 'desired_state_idle' : 'no_channel' });
        continue;
      }
      registry.mint(guildId).lifecycle.join(channel).catch(() => {
        // Join failed: already logged by the lifecycle, and its own
        // `onJoinFailed` hook (wired in `makeCore`, above) has already
        // dropped this core from the registry — /health does not carry it.
        // Nothing left to do here; compose's restart policy gives the retry.
      });
    }
    if (!guilds.length) log('boot_idle', { reason: 'no_persisted_guilds' });
  } else {
    log('boot_idle', { reason: 'healthy-but-idle', missing: 'DISCORD_BOT_TOKEN' });
  }

  async function shutdown(signal) {
    log('shutdown', { signal });
    await Promise.allSettled(registry.values().map((c) => c.lifecycle.shutdown()));
    server.close(() => process.exit(0));
    // Don't hang on a half-open voice socket.
    setTimeout(() => process.exit(0), 3000).unref();
  }
  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  // Checked here, not at module scope: test/service.test.mjs imports this file,
  // and a module-level exit would take the whole test run down with it.
  if (!BRIDGE_TOKEN) {
    console.error('[voc] NORA_BRIDGE_TOKEN is not set; refusing to start an unauthenticated audio controller');
    process.exit(2);
  }
  await main();
}
