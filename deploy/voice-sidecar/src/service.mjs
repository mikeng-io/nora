// Production entry for the voice receive sidecar (stage 2 of
// design/voice-channel/05-sidecar-receive.md).
//
//   node src/service.mjs
//
// What it does, in order:
//   1. serves a loopback HTTP /health endpoint (process liveness — this is
//      what the Dockerfile HEALTHCHECK and the compose healthcheck curl);
//   2. if DISCORD_BOT_TOKEN + GUILD_ID + CHANNEL_ID are all set, joins the
//      voice channel and pumps per-user DAVE-decrypted PCM to
//      PUMP_HOST:PUMP_PORT (the wake-gate seam, TRANSPORT.md).
//
// Health semantics: /health is LIVENESS, and it is deliberately 200 while
// the service is healthy-but-idle. A missing token is a configuration
// posture (voice receive not enabled on this deployment), not a process
// failure — the dumb-pipe design has nothing to fail closed INTO. The
// `voice` field in the payload reports the receive state
// (idle | connecting | connected | error) so an operator can tell the two
// apart without reading logs.
//
// Privacy posture: audio is transient. PCM is forwarded over the TCP seam
// and never written to disk here — WAV recording exists only in spike.mjs.

import http from 'node:http';
import { join, createPump } from './recv.js';
import { createPlayer, parsePlayBody } from './player.js';

const HOST = process.env.HOST ?? '127.0.0.1';
const PORT = Number(process.env.PORT ?? 4600);
const PUMP_HOST = process.env.PUMP_HOST ?? '127.0.0.1';
const PUMP_PORT = Number(process.env.PUMP_PORT ?? 4599);

const TOKEN = process.env.DISCORD_BOT_TOKEN;
const GUILD_ID = process.env.GUILD_ID;
const CHANNEL_ID = process.env.CHANNEL_ID;

let voiceState = 'idle'; // idle | connecting | connected | error
let lastError = null;
const perUser = new Map(); // userId -> {frames, bytes} since process start

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    const users = {};
    for (const [userId, s] of perUser) users[userId] = s;
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(
      JSON.stringify({
        status: 'ok',
        voice: voiceState,
        ...(player ? { player: { playing: player.playing(), queued: player.queued() } } : {}),
        ...(lastError ? { last_error: lastError } : {}),
        pump: { host: PUMP_HOST, port: PUMP_PORT },
        users,
      }),
    );
    return;
  }
  if (req.method === 'POST' && req.url === '/play') {
    // Stage 3 closure: Nora POSTs a synthesized reply (mp3 base64) and this
    // plays it into the same channel it is listening in. No auth, same
    // posture as /health: reachable only on the private nora network, and the
    // only caller is Nora herself (or an operator on the same box).
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 2 * 1024 * 1024) {
        req.destroy();
        return;
      }
    });
    req.on('end', () => {
      if (!player || voiceState !== 'connected') {
        res.writeHead(503, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: 'not connected to a voice channel' }));
        return;
      }
      let parsed;
      try {
        parsed = parsePlayBody(JSON.parse(body || '{}'));
      } catch {
        parsed = { error: 'invalid json' };
      }
      if (parsed.error) {
        res.writeHead(400, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: parsed.error }));
        return;
      }
      const accepted = player.play(parsed.audio, parsed.media_type);
      if (!accepted) {
        res.writeHead(503, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: 'play queue full' }));
        return;
      }
      res.writeHead(202, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ status: 'queued' }));
    });
    return;
  }
  res.writeHead(404, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ error: 'not found' }));
});

server.on('error', (err) => {
  // The health endpoint IS the liveness contract (HEALTHCHECK curls it); if
  // it cannot bind, liveness is genuinely unavailable — die with a clear
  // log line and let the container's restart policy retry.
  console.error(`[service] health endpoint failed on ${HOST}:${PORT}: ${err.message}`);
  process.exit(1);
});
server.listen(PORT, HOST, () => {
  console.log(`[service] health endpoint on http://${HOST}:${PORT}/health`);
});

let leave = null;
let pump = null;
let player = null;

async function startReceive() {
  voiceState = 'connecting';
  pump = createPump({ host: PUMP_HOST, port: PUMP_PORT });
  const joined = await join(GUILD_ID, CHANNEL_ID, TOKEN);
  leave = joined.leave;
  const bus = joined.bus;
  player = createPlayer(joined.connection);
  voiceState = 'connected';
  console.log(`[service] joined channel ${CHANNEL_ID}; forwarding PCM to ${PUMP_HOST}:${PUMP_PORT}`);

  bus.on('listening', (userId) => {
    console.log(`[service] listening: user ${userId}`);
  });
  bus.on('pcm', (userId, pcm) => {
    const s = perUser.get(userId) ?? { frames: 0, bytes: 0 };
    s.frames += 1;
    s.bytes += pcm.length;
    perUser.set(userId, s);
    pump(pcm, userId);
  });
  bus.on('streamError', (userId, err) => {
    console.error(`[service] stream error for user ${userId}: ${err.message} (will resubscribe on next speech)`);
  });
}

if (TOKEN && GUILD_ID && CHANNEL_ID) {
  startReceive().catch((err) => {
    // Join failed (bad token, wrong channel, gateway down). Log it, mark the
    // state, stay alive: /health keeps reporting liveness and the operator
    // sees voice=error. restart: unless-stopped in compose gives the retry.
    voiceState = 'error';
    lastError = String(err?.message ?? err);
    console.error(`[service] voice join failed: ${lastError}`);
  });
} else {
  const missing = [
    ['DISCORD_BOT_TOKEN', TOKEN],
    ['GUILD_ID', GUILD_ID],
    ['CHANNEL_ID', CHANNEL_ID],
  ]
    .filter(([, v]) => !v)
    .map(([k]) => k)
    .join(', ');
  console.log(`[service] healthy-but-idle: ${missing} not set — voice receive disabled until they are`);
}

async function shutdown(signal) {
  console.log(`[service] ${signal} received, shutting down`);
  pump?.close();
  if (leave) await leave();
  server.close(() => process.exit(0));
  // Don't hang on a half-open voice socket.
  setTimeout(() => process.exit(0), 3000).unref();
}
process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
