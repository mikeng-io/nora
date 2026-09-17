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

// --- Wake-gate seam ------------------------------------------------------
// The codec and the pump moved to `codec.mjs` / `pump.mjs` (2026-09-17) so
// they are testable without a discord.js tree; re-exported here because
// TRANSPORT.md and the test files name `recv.js` as the seam's home.
export { encodeHello, encodeFrame, createFrameParser } from './codec.mjs';
export { createPump } from './pump.mjs';


