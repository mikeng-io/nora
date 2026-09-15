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
 * @returns {Promise<{bus: EventEmitter, leave: () => Promise<void>}>}
 *   bus events:
 *     'listening'  (userId)              - a per-user stream attached
 *     'pcm'        (userId, Buffer)      - decoded PCM chunk (s16le 48k stereo)
 *     'streamError'(userId, Error)       - a per-user stream died; it will
 *                                          resubscribe on next speaking start
 */
export async function join(guildId, channelId, token) {
  const bus = new EventEmitter();
  const client = new Client({
    intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildVoiceStates],
  });
  await client.login(token);
  await new Promise((resolve, reject) => {
    if (client.isReady()) return resolve();
    client.once('clientReady', resolve);
    client.once('error', reject);
  });

  const guild = client.guilds.cache.get(guildId) ?? (await client.guilds.fetch(guildId));
  const connection = joinVoiceChannel({
    channelId,
    guildId,
    adapterCreator: guild.voiceAdapterCreator,
    selfDeaf: false,
    selfMute: true,
  });
  await entersState(connection, VoiceConnectionStatus.Ready, 30_000);

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
  return { bus, leave };
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
 */
export function createPump({ host = '127.0.0.1', port = 4599, maxPending = 200 } = {}) {
  let socket = null;
  let connecting = false;
  let pending = [];

  function ensureSocket() {
    if (socket || connecting) return;
    connecting = true;
    const s = net.connect({ host, port });
    s.once('connect', () => {
      connecting = false;
      socket = s;
      for (const frame of pending) s.write(frame);
      pending = [];
    });
    const drop = () => {
      connecting = false;
      socket = null;
      pending = [];
    };
    s.once('error', drop);
    s.once('close', drop);
  }

  function pump(pcm, userId) {
    const frame = encodeFrame(userId, pcm);
    if (socket) {
      socket.write(frame);
      return;
    }
    ensureSocket();
    if (connecting) {
      // Mid-handshake: hold briefly, drop oldest beyond the cap.
      pending.push(frame);
      if (pending.length > maxPending) pending.shift();
    }
    // No listener and not connecting: drop the frame.
  }
  pump.close = () => socket?.destroy();
  return pump;
}
