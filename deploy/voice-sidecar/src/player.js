// TTS playback for the voice sidecar (stage 3 closure): Nora synthesizes a
// reply (MiniMax mp3), POSTs it here, and this plays it into the SAME voice
// channel the wake gate listens in — one bot, one connection, receive AND
// play. @discordjs/voice's AudioPlayer is the natural fit: it already owns
// the silence/state machine and its resource pipeline transcodes mp3 → opus
// through ffmpeg (the container ships ffmpeg for exactly this).
//
// Concurrency: a reply that arrives while one is already playing is QUEUED —
// two TTS replies must never overlap on one channel. The queue is bounded
// (max 4) and a full queue drops the NEWEST attempt with a 503, so a burst
// of replies degrades into silence for the excess, never a memory grow.

import {
  createAudioPlayer,
  createAudioResource,
  NoSubscriberBehavior,
} from '@discordjs/voice';
import { Readable } from 'node:stream';

const MAX_QUEUE = 4;

export function createPlayer(connection) {
  const player = createAudioPlayer({
    behaviors: { noSubscriber: NoSubscriberBehavior.Pause },
  });
  connection.subscribe(player);
  const queue = [];

  player.on('error', (err) => {
    // A single resource failing must not wedge the queue. Log and play the
    // next item; the caller (Nora) already treats a lost reply as text-only
    // fallback, so there is nothing to escalate here.
    console.error(`[player] audio error: ${err.message}`);
    next();
  });
  player.on('idle', () => next());

  function next() {
    const item = queue.shift();
    if (!item) return;
    try {
      player.play(createAudioResource(Readable.from([item.audio]), {
        inputType: item.mediaType === 'audio/wav' ? 'wav' : 'unknown',
        inlineVolume: false,
      }));
    } catch (err) {
      console.error(`[player] failed to start queued audio: ${err.message}`);
    }
  }

  function play(audio, mediaType = 'audio/mpeg') {
    if (queue.length >= MAX_QUEUE) return false;
    queue.push({ audio, mediaType });
    if (player.state.status === 'idle') next();
    return true;
  }

  return {
    play,
    playing: () => player.state.status === 'playing',
    queued: () => queue.length,
  };
}

/** One POST /play — used by service.mjs. Accepts { audio_b64, media_type }. */
export function parsePlayBody(body) {
  if (!body || typeof body !== 'object') return { error: 'invalid body' };
  const { audio_b64, media_type } = body;
  if (typeof audio_b64 !== 'string' || !audio_b64) {
    return { error: 'audio_b64 (base64) is required' };
  }
  let audio;
  try {
    audio = Buffer.from(audio_b64, 'base64');
  } catch {
    return { error: 'audio_b64 is not valid base64' };
  }
  if (!audio.length) return { error: 'audio_b64 decoded to empty bytes' };
  return { audio, media_type: typeof media_type === 'string' ? media_type : 'audio/mpeg' };
}
