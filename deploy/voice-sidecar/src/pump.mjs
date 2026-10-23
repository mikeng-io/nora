// The wake-gate PUMP: forwards per-user PCM out to the gate over the seam in
// `codec.mjs`. Split from recv.js 2026-09-17 for the same reason the codec
// was — recv.js imports discord.js at module scope, so `pump.test.mjs` needed
// a node_modules tree and had stopped running locally. A falsification probe
// then found the gap the hard way: suppressing the hello frame reddened
// nothing, because nothing could test this file.

import net from 'node:net';

import { encodeHello, encodeFrame } from './codec.mjs';

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
export function createPump({ guildId, token, host = '127.0.0.1', port = 4599, maxPending = 200, maxUserBacklog = 16, connectImpl = net.connect } = {}) {
  let socket = null;
  // Validated HERE, not in the connect handler: a throw inside `'connect'` is
  // an uncaught exception on the event loop and would take the whole process
  // down. A pump without a guild cannot produce attributable audio, so there
  // is no useful degraded mode to fall back to.
  if (typeof guildId !== 'string' || !guildId) {
    throw new Error('createPump needs a guildId — a hello-less connection is dropped by the gate');
  }
  // The gate refuses a hello without the shared token, so a pump built without
  // one could never deliver audio. Refused here, for the same reason as above.
  if (typeof token !== 'string' || !token) {
    throw new Error('createPump needs the service token — the wake gate refuses an unauthenticated hello');
  }
  let connecting = false;
  let closed = false;
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
      // THE HELLO GOES FIRST, before any held audio — a gate that received
      // frames before knowing the guild would have to buffer them or drop
      // them, and dropping an utterance's onset is exactly what `maxPending`
      // exists to prevent. Written directly rather than through
      // `writeCounted`: it is not a user's audio and must not enter a user's
      // backlog accounting.
      // A LATE connect on a CLOSED pump writes nothing — not even this. The
      // socket is already destroyed, so the write is inert, but announcing a
      // guild for a connection that will never carry audio is a lie in the
      // one frame a gate uses to decide what it is listening to.
      if (closed) return;
      // Unconditional: a hello-less connection is dropped wholesale by the
      // gate, so a falsy guildId would silently produce a pump forwarding
      // audio nobody can attribute — a failure in another container, with
      // nothing here to point at it. `createPump` refuses one outright.
      s.write(encodeHello(guildId, token));
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
    closed = true;
    socket?.destroy();
    connectingSocket?.destroy(); // a close mid-handshake must not leak the socket
  };
  pump.stats = () => ({
    backlog_dropped: backlogDropped,
    no_listener_dropped: noListenerDropped,
  });
  return pump;
}
