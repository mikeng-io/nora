// Receive-only voice sidecar core: join a Discord voice channel, subscribe
// per-user Opus streams, DAVE-decrypt (inside @discordjs/voice's
// VoiceReceiver.parsePacket), decode Opus -> PCM, and expose both the PCM
// events and a pump() that forwards PCM to a local TCP listener.
//
// Dumb pipe: no wake-word, no STT, no AI. See DAVE-NOTES.md for the decrypt
// path this relies on.

import { EventEmitter } from 'node:events';
import net from 'node:net';
import { Client, GatewayIntentBits } from 'discord.js';
import {
  joinVoiceChannel,
  entersState,
  VoiceConnectionStatus,
  EndBehaviorType,
} from '@discordjs/voice';
import prism from 'prism-media';

export const PCM_RATE = 48000;
export const PCM_CHANNELS = 2;
export const PCM_FRAME_BYTES = 3840; // 20 ms @ 48 kHz stereo s16le

/**
 * Join a voice channel and emit per-user PCM.
 *
 * @param {string} guildId
 * @param {string} channelId
 * @param {string} token - bot token; used for login only, never logged
 * @param {object} [seams] - injected Discord touches (tests only; the
 *   production caller passes none)
 * @returns {Promise<{bus: EventEmitter, leave: () => Promise<void>}>}
 *   bus events:
 *     'listening'  (userId)              - a per-user stream attached
 *     'pcm'        (userId, Buffer)      - decoded PCM chunk (s16le 48k stereo)
 *     'streamError'(userId, Error)       - a per-user stream died; it will
 *                                          resubscribe on next speaking start
 *
 * A join that fails at ANY construction step destroys what it already built
 * (connection + client): a retry must never share the process with a zombie
 * from the failed attempt (2026-09-16 audit CN-2).
 */
export async function join(guildId, channelId, token, {
  createClient = () =>
    new Client({
      intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
    }),
  joinVoiceChannelImpl = joinVoiceChannel,
  entersStateImpl = entersState,
  readyTimeoutMs = 30_000,
} = {}) {
  const bus = new EventEmitter();
  const client = createClient();
  let connection = null;
  try {
    await client.login(token);
    await new Promise((resolve, reject) => {
      if (client.isReady()) return resolve();
      client.once('clientReady', resolve);
      client.once('error', reject);
    });

    const guild = client.guilds.cache.get(guildId) ?? (await client.guilds.fetch(guildId));
    connection = joinVoiceChannelImpl({
      channelId,
      guildId,
      adapterCreator: guild.voiceAdapterCreator,
      selfDeaf: false,
      // Must be audible to PLAY TTS (stage 3+ playback). Discord.js never
      // receives the bot's own audio (it speaks via its own socket, and the
      // receiver's speaking map excludes self), so un-muting does not feed
      // Nora's replies back into the wake gate.
      selfMute: false,
    });
    await entersStateImpl(connection, VoiceConnectionStatus.Ready, readyTimeoutMs);
  } catch (err) {
    try {
      connection?.destroy();
    } catch {
      /* already destroyed */
    }
    try {
      client.destroy();
    } catch {
      /* already destroyed */
    }
    throw err;
  }

  const subscribed = new Set();
  connection.receiver.speaking.on('start', (userId) => {
    if (subscribed.has(userId)) return;
    subscribed.add(userId);
    // Manual end behavior: one long-lived stream per user; the library's own
    // receive path destroys the stream on a decrypt/parse error, and we
    // resubscribe when the user speaks again.
    const opusStream = connection.receiver.subscribe(userId, {
      end: { behavior: EndBehaviorType.Manual },
    });
    const decoder = new prism.opus.Decoder({
      channels: PCM_CHANNELS,
      rate: PCM_RATE,
      frameSize: 960,
    });
    const onError = (err) => {
      subscribed.delete(userId);
      opusStream.unpipe(decoder);
      decoder.destroy();
      bus.emit('streamError', userId, err);
    };
    opusStream.once('error', onError);
    decoder.once('error', onError);
    opusStream.pipe(decoder);
    decoder.on('data', (pcm) => bus.emit('pcm', userId, pcm));
    bus.emit('listening', userId);
  });

  async function leave() {
    connection.destroy();
    client.destroy();
  }
  return { bus, leave, connection, client };
}

// --- Wake-gate seam: TCP with length-prefixed binary frames ---------------
// Frame: [u32be payload_len][u8 user_id_len][user_id utf8][pcm bytes]
// See TRANSPORT.md for why.

export function encodeFrame(userId, pcm) {
  const id = Buffer.from(String(userId), 'utf8');
  if (id.length > 255) throw new Error('userId too long for frame');
  const payload = Buffer.concat([Buffer.from([id.length]), id, pcm]);
  const header = Buffer.alloc(4);
  header.writeUInt32BE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

/**
 * Incremental parser for the frame format above. Feed it arbitrary TCP
 * chunks; it calls onFrame(userId, pcmBuffer) per complete frame.
 */
export function createFrameParser(onFrame) {
  let buf = Buffer.alloc(0);
  return function feed(chunk) {
    buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
    for (;;) {
      if (buf.length < 4) return;
      const payloadLen = buf.readUInt32BE(0);
      if (buf.length < 4 + payloadLen) return;
      const payload = buf.subarray(4, 4 + payloadLen);
      const idLen = payload[0];
      const userId = payload.subarray(1, 1 + idLen).toString('utf8');
      const pcm = payload.subarray(1 + idLen);
      buf = buf.subarray(4 + payloadLen);
      onFrame(userId, pcm);
    }
  };
}

/**
 * Create a pump(pcm, userId) that forwards PCM to a local TCP listener
 * (the future Python wake gate). Reconnects lazily. Frames are dropped only
 * when no listener is up at all — during the (sub-millisecond) connect
 * handshake a small bounded buffer (200 frames ≈ 4 s) holds audio so the
 * start of an utterance is not lost; when the wake gate is genuinely down,
 * frames are dropped rather than queued unboundedly.
 *
 * Connected-state bound (B12, design/voice-channel/07 §7): once the socket
 * is up, each user gets a backlog bound of `maxUserBacklog` frames (16 ×
 * 20 ms by default) measured as written-but-not-yet-flushed. A frame that
 * would exceed the bound is DROPPED and counted; pump.stats() exposes the
 * drop counters for /voice/status. The wake gate degrades into gaps for a
 * slow consumer, never into a memory grow.
 */
export function createPump({ host = '127.0.0.1', port = 4599, maxPending = 200, maxUserBacklog = 16, connectImpl = net.connect } = {}) {
  let socket = null;
  let connecting = false;
  let connectingSocket = null; // mid-handshake socket — close() must reach it too
  let pending = [];
  const userBacklog = new Map(); // userId -> frames written but not yet flushed
  let backlogDropped = 0;
  let noListenerDropped = 0;

  function writeCounted(userId, frame) {
    const backlog = userBacklog.get(userId) ?? 0;
    if (backlog >= maxUserBacklog) {
      backlogDropped += 1;
      return;
    }
    userBacklog.set(userId, backlog + 1);
    socket.write(frame, () => {
      const n = userBacklog.get(userId) ?? 0;
      if (n > 0) userBacklog.set(userId, n - 1);
    });
  }

  function ensureSocket() {
    if (socket || connecting) return;
    connecting = true;
    const s = connectImpl({ host, port });
    connectingSocket = s;
    s.once('connect', () => {
      connecting = false;
      connectingSocket = null;
      socket = s;
      const held = pending;
      pending = [];
      for (const { userId, frame } of held) writeCounted(userId, frame);
    });
    const drop = () => {
      connecting = false;
      connectingSocket = null;
      socket = null;
      pending = [];
    };
    s.once('error', drop);
    s.once('close', drop);
  }

  function pump(pcm, userId) {
    const frame = encodeFrame(userId, pcm);
    if (socket) {
      writeCounted(userId, frame);
      return;
    }
    ensureSocket();
    if (connecting) {
      // Mid-handshake: hold briefly; beyond the cap the listener is
      // genuinely down — drop the oldest and count it.
      pending.push({ userId, frame });
      if (pending.length > maxPending) {
        pending.shift();
        noListenerDropped += 1;
      }
    }
  }
  pump.close = () => {
    socket?.destroy();
    connectingSocket?.destroy(); // a close mid-handshake must not leak the socket
  };
  pump.stats = () => ({
    backlog_dropped: backlogDropped,
    no_listener_dropped: noListenerDropped,
  });
  return pump;
}
