#!/usr/bin/env node
/**
 * Nora WhatsApp Bridge
 *
 * Node 20 daemon that connects to WhatsApp via Baileys and exposes a small
 * HTTP + SSE surface for the Nora Python process. The wire contract is
 * pinned in design/whatsapp-adoption/03-transport-contract.md — every route,
 * every SSE field, the drop-and-count rule, and the health body are defined
 * there, not here.
 *
 * Adapted from Hermes' scripts/whatsapp-bridge/ (MIT). Deliberately NOT
 * copied: express (built-in http instead), qrcode-terminal (pairing-code
 * flow instead), long-poll /messages (SSE instead), the silent-overflow
 * messageQueue (drop-and-count instead), media custody, allowlist/mode
 * gating, polls, location, typing, read receipts.
 *
 * Endpoints:
 *   GET  /health      - liveness + drop counter (always 200 while serving)
 *   GET  /events      - SSE stream; replays bounded offline buffer on connect
 *   GET  /self        - the bot's own id forms (503 until socket open)
 *   POST /send        - { jid, text }
 *   POST /send-media  - { jid, media_type, data_base64, filename?, caption? }
 *   POST /edit        - { jid, id, text }
 *   POST /react       - { jid, message_id, emoji, remove? }
 *
 * Usage:
 *   node bridge.js --port 18081 --session /app/session
 *   node bridge.js --pair --phone 85290000100 --session /app/session
 */

import http from 'node:http';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, proto } from '@whiskeysockets/baileys';

import {
  authorizedBridgeRequest,
  buildLidMap,
  buildMediaPayload,
  createEventBuffer,
  createRecentMessageCache,
  createReconnectScheduler,
  createSilentLogger,
  ensureDir,
  extractMessageEvent,
  getMessageContent,
  decryptSecretEdit,
  invertLidMap,
  isValidTargetJid,
  jidUserDigits,
  normalizeJid,
  redactJid,
  resolveIdentity,
  timestampMs,
} from './bridge_helpers.js';

// -- CLI args ---------------------------------------------------------------

const args = process.argv.slice(2);
function getArg(name, defaultVal) {
  const idx = args.indexOf(`--${name}`);
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : defaultVal;
}

const PORT = parseInt(getArg('port', '18081'), 10);
const SESSION_DIR = path.resolve(getArg('session', path.join(process.env.HOME || '~', '.nora', 'whatsapp', 'session')));
const PAIR_MODE = args.includes('--pair');
const PAIR_PHONE = String(getArg('phone', '')).replace(/\D/g, '');
const BRIDGE_TOKEN = process.env.NORA_BRIDGE_TOKEN || '';

if (PAIR_MODE && !PAIR_PHONE) {
  console.error('[bridge] --pair requires --phone <digits> (international format, no +)');
  process.exit(2);
}
if (!PAIR_MODE && !BRIDGE_TOKEN) {
  console.error('[bridge] NORA_BRIDGE_TOKEN is not set; refusing to start an unauthenticated bridge');
  process.exit(2);
}

function log(line) {
  console.log(`[bridge] ${line}`);
}

// -- State ------------------------------------------------------------------

ensureDir(SESSION_DIR);

const STARTED_AT = Date.now();
let sock = null;
let connected = false;
// paired = usable auth state exists. Seeded from the presence of creds.json;
// cleared if WhatsApp tells us loggedOut; set on connection open.
let paired = existsSync(path.join(SESSION_DIR, 'creds.json'));
let loggedOut = false;

// LID <-> phone maps, rebuilt from session files on creds.update and
// on a periodic rescan (Baileys writes new lid-mapping-*.json files as it
// resolves identities; the maps must track them without a restart). BOTH
// directions: the adapter's mention contract tests membership against both
// id forms, whichever JID spelling Baileys happened to deliver.
let lidToPhone = buildLidMap(SESSION_DIR);
let phoneToLid = invertLidMap(lidToPhone);

const recentMessages = createRecentMessageCache(1000);

// The drop notice goes to the NEXT connecting SSE client exactly once; the
// counter itself stays on /health permanently.
let dropNoticePending = false;

const buffer = createEventBuffer(500, {
  onDrop: (dropped) => {
    dropNoticePending = true;
    log(`event buffer overflow: dropped oldest (dropped_events=${dropped})`);
  },
});

const sseClients = new Set();

// -- SSE fan-out + buffering (contract §dropped) ------------------------------

function sseWrite(res, event) {
  res.write(`data: ${JSON.stringify(event)}\n\n`);
}

function emitEvent(event) {
  if (sseClients.size > 0) {
    for (const res of sseClients) {
      try { sseWrite(res, event); } catch { /* dead socket; close handler cleans up */ }
    }
  } else {
    buffer.push(event);
  }
}

function broadcastHeartbeat() {
  if (sseClients.size === 0) return;
  const event = { type: 'heartbeat', ts_ms: Date.now(), connected, paired };
  for (const res of sseClients) {
    try { sseWrite(res, event); } catch { /* ignore */ }
  }
}
const heartbeatTimer = setInterval(broadcastHeartbeat, 30000);
heartbeatTimer.unref();

// Refresh the LID map periodically — mapping files can appear at any time.
const lidScanTimer = setInterval(() => {
  lidToPhone = buildLidMap(SESSION_DIR);
  phoneToLid = invertLidMap(lidToPhone);
}, 30000);
lidScanTimer.unref();

// -- Baileys socket ------------------------------------------------------------

const baileysLogger = createSilentLogger();
const scheduleReconnect = createReconnectScheduler(() => startSocket(), {
  baseDelayMs: 1000,
  maxDelayMs: 60000,
  log,
});

let pairingCodeRequested = false;

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  let version = null;
  try {
    const fetched = await Promise.race([
      fetchLatestBaileysVersion(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('version fetch timed out')), 15000)),
    ]);
    version = fetched.version;
  } catch (err) {
    log(`Baileys version fetch failed (${err?.message || err}); using library default`);
  }

  sock = makeWASocket({
    ...(version ? { version } : {}),
    auth: state,
    logger: baileysLogger,
    printQRInTerminal: false,
    browser: ['Nora', 'Chrome', '120.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    // Baileys 7.x needs this to complete the E2EE retry handshake for
    // messages that arrive before their session is established.
    getMessage: async () => ({ conversation: '' }),
  });

  sock.ev.on('creds.update', () => {
    saveCreds();
    lidToPhone = buildLidMap(SESSION_DIR);
    phoneToLid = invertLidMap(lidToPhone);
  });

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr && PAIR_MODE) {
      // Fallback only: the pairing-code flow is primary. The raw QR string
      // is printed so an operator can render it elsewhere if needed.
      log('QR string emitted by WhatsApp (fallback pairing path):');
      console.log(qr);
    }

    if (PAIR_MODE && !pairingCodeRequested && sock && !state.creds.registered) {
      pairingCodeRequested = true;
      // Baileys requires the socket to be mid-handshake before this call.
      setTimeout(async () => {
        try {
          const code = await sock.requestPairingCode(PAIR_PHONE);
          log(`pairing code for +${PAIR_PHONE}: ${code}`);
          log('on the phone: Linked Devices -> Link with phone number -> enter the code');
        } catch (err) {
          log(`requestPairingCode failed (${err?.message || err}); use the QR string above instead`);
        }
      }, 3000);
    }

    if (connection === 'open') {
      connected = true;
      paired = true;
      loggedOut = false;
      scheduleReconnect.reset();
      const me = sock?.user;
      log(`connected phone=${jidUserDigits(me?.id) || '?'} lid=${jidUserDigits(me?.lid) || '?'}`);
      if (PAIR_MODE) {
        log('pairing complete; credentials saved to session dir');
        // Give Baileys a moment to flush creds before exiting.
        setTimeout(() => process.exit(0), 2500);
      }
    } else if (connection === 'close') {
      connected = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode
        ?? lastDisconnect?.error?.output?.payload?.statusCode;
      if (statusCode === DisconnectReason.loggedOut) {
        // Do NOT delete the session, do NOT reconnect-loop. Keep serving
        // HTTP so /health can report paired:false and the operator can
        // re-pair with the foreground command.
        loggedOut = true;
        paired = false;
        log('LOGGED OUT by WhatsApp — session kept on disk; re-pair with: node bridge.js --pair --phone <digits>');
        if (PAIR_MODE) process.exit(1);
      } else {
        log(`connection closed (reason=${statusCode ?? 'unknown'}); reconnecting with backoff`);
        scheduleReconnect.scheduleReconnect();
      }
    }
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    // notify only — never history ('append'/'history' upserts or
    // messaging-history.set), or first pairing would flood the audit trail
    // with replayed history.
    if (type !== 'notify') return;

    for (const msg of messages || []) {
      if (!msg?.key?.id || !msg.message) continue;
      recentMessages.remember(msg);
      let source = msg;
      if (getMessageContent(msg)?.secretEncryptedMessage) {
        // A phone's edit, encrypted with the original's message secret.
        let opened;
        try {
          opened = decryptSecretEdit(msg, {
            recentCache: recentMessages, decode: (b) => proto.Message.decode(b),
            lidToPhone, phoneToLid,
          });
        } catch (err) {
          // Untrusted input: whatever it does, it must not take the rest of
          // this batch down with it.
          opened = { reason: `error:${err?.code || err?.name || 'unknown'}` };
        }
        if (!opened.msg) {
          log(`secret edit not decrypted id=${msg.key.id} chat=${redactJid(msg.key.remoteJid)} reason=${opened.reason}${opened.shape ? ` shape=[${opened.shape}]` : ''}`);
          continue;
        }
        log(`secret edit decrypted id=${msg.key.id} chat=${redactJid(msg.key.remoteJid)} shape=[${opened.shape}]`);
        source = opened.msg;
      }
      const event = extractMessageEvent(source, { lidToPhone, phoneToLid, recentCache: recentMessages });
      if (!event) {
        // WHAT WAS DROPPED, by SHAPE only — the message's own keys and the
        // unwrapped content's, never a value. A phone's edit produced no event
        // at all on 2026-09-24 and nothing here said what it had looked like,
        // so there was no way to tell a wrapper this function does not unwrap
        // from an encrypted payload it cannot read.
        const outer = Object.keys(msg.message || {}).join(',');
        const inner = Object.keys(getMessageContent(msg) || {}).join(',');
        log(`dropped id=${msg.key.id} chat=${redactJid(msg.key.remoteJid)} outer=[${outer}] inner=[${inner}]`);
        continue;
      }
      log(`message id=${event.id || '?'} chat=${redactJid(event.chat_jid)} from=${redactJid(event.sender_jid)} group=${event.is_group} from_me=${event.from_me} edit=${!!event.edit_of} type=${event.type} media=${(event.media || []).length} buffered=${sseClients.size === 0}`);
      emitEvent(event);
    }
  });
}

// -- HTTP surface -------------------------------------------------------------

const MAX_BODY_BYTES = 100 * 1024 * 1024;

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function readJsonBody(req) {
  const raw = await readBody(req);
  if (raw.length === 0) return {};
  return JSON.parse(raw.toString('utf8'));
}

function requireSocket(res) {
  if (!sock || !connected) {
    sendJson(res, 503, { error: 'not_connected' });
    return false;
  }
  return true;
}

function validJidOr400(res, jid) {
  if (typeof jid !== 'string' || !isValidTargetJid(jid)) {
    sendJson(res, 400, { error: 'invalid jid (must end @s.whatsapp.net, @g.us, or @lid)' });
    return false;
  }
  return true;
}

function sentResult(sent) {
  return { ok: true, id: sent?.key?.id || null, timestamp_ms: timestampMs(sent?.messageTimestamp) };
}

// Serialise sendMessage calls across concurrent HTTP handlers: overlapping
// sends on one Baileys socket can misroute at the protocol level (Hermes
// #33360). One in flight at a time.
let sendQueue = Promise.resolve();
function enqueueSend(fn) {
  const task = sendQueue.then(() => fn(), () => fn());
  sendQueue = task.catch(() => {});
  return task;
}

const SEND_TIMEOUT_MS = 60000;
function sendWithTimeout(jid, payload) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('sendMessage timed out after 60s')), SEND_TIMEOUT_MS);
  });
  return enqueueSend(() =>
    Promise.race([sock.sendMessage(jid, payload), timeout]).finally(() => clearTimeout(timer))
  );
}

function handleEvents(req, res) {
  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
  });
  res.write(':\n\n');

  if (dropNoticePending && buffer.dropped > 0) {
    sseWrite(res, { type: 'dropped', dropped_total: buffer.dropped });
    dropNoticePending = false;
    log(`sent dropped notice (dropped_total=${buffer.dropped}) to new SSE client`);
  }
  // Register BEFORE draining: events that arrive after this point broadcast
  // live instead of entering the buffer, so snapshot+clear cannot lose one.
  sseClients.add(res);
  const replay = buffer.snapshot();
  buffer.clear();
  for (const event of replay) {
    sseWrite(res, event);
  }

  log(`SSE client connected (clients=${sseClients.size}, replayed=${replay.length})`);
  req.on('close', () => {
    sseClients.delete(res);
    log(`SSE client disconnected (clients=${sseClients.size})`);
  });
}

function handleHealth(req, res) {
  sendJson(res, 200, {
    ok: true,
    connected,
    paired,
    dropped_events: buffer.dropped,
    buffered_events: buffer.size,
    uptime_seconds: Math.floor((Date.now() - STARTED_AT) / 1000),
  });
}

function handleSelf(req, res) {
  if (!requireSocket(res)) return;
  const me = sock?.user || {};
  const id = normalizeJid(me.id) || null;
  const lidJid = normalizeJid(me.lid) || null;
  // Contract (design/whatsapp-adoption/03-transport-contract.md): `phone` and
  // `lid` are DIGIT strings — the Python adapter feeds them straight into its
  // phone_form()/lid_form() normalization. The JID spellings ride along under
  // their own names for the operator.
  sendJson(res, 200, {
    ok: true,
    jid: id,
    lid_jid: lidJid,
    phone: id ? jidUserDigits(id) || null : null,
    lid: lidJid ? jidUserDigits(lidJid) || null : null,
    name: me.name || me.verifiedName || null,
  });
}

async function handleSend(req, res) {
  if (!requireSocket(res)) return;
  const body = await readJsonBody(req);
  const { jid, text } = body || {};
  if (!validJidOr400(res, jid)) return;
  if (typeof text !== 'string' || text.length === 0) {
    return sendJson(res, 400, { error: 'text is required' });
  }
  try {
    const sent = await sendWithTimeout(jid, { text });
    if (sent?.key?.id) recentMessages.remember(sent);
    log(`send ok jid=${redactJid(jid)} id=${sent?.key?.id || '?'}`);
    sendJson(res, 200, sentResult(sent));
  } catch (err) {
    sendJson(res, 500, { error: String(err?.message || err) });
  }
}

async function handleSendMedia(req, res) {
  if (!requireSocket(res)) return;
  const body = await readJsonBody(req);
  const { jid, media_type: mediaType, data_base64: dataBase64, filename, caption } = body || {};
  if (!validJidOr400(res, jid)) return;
  if (typeof dataBase64 !== 'string' || dataBase64.length === 0) {
    return sendJson(res, 400, { error: 'data_base64 is required' });
  }
  let bufferData;
  try {
    bufferData = Buffer.from(dataBase64, 'base64');
  } catch {
    return sendJson(res, 400, { error: 'data_base64 is not valid base64' });
  }
  if (bufferData.length === 0) {
    return sendJson(res, 400, { error: 'data_base64 decoded to zero bytes' });
  }
  const payload = buildMediaPayload({
    mediaType,
    buffer: bufferData,
    filename: typeof filename === 'string' ? filename : undefined,
    caption: typeof caption === 'string' && caption ? caption : undefined,
    mimetype: typeof body.mimetype === 'string' ? body.mimetype : undefined,
  });
  if (!payload) {
    return sendJson(res, 400, { error: 'media_type must be image|video|audio|sticker|document' });
  }
  try {
    const sent = await sendWithTimeout(jid, payload);
    if (sent?.key?.id) recentMessages.remember(sent);
    log(`send-media ok jid=${redactJid(jid)} kind=${mediaType} bytes=${bufferData.length} id=${sent?.key?.id || '?'}`);
    sendJson(res, 200, sentResult(sent));
  } catch (err) {
    sendJson(res, 500, { error: String(err?.message || err) });
  }
}

async function handleEdit(req, res) {
  if (!requireSocket(res)) return;
  const body = await readJsonBody(req);
  const { jid, id, text } = body || {};
  if (!validJidOr400(res, jid)) return;
  if (typeof id !== 'string' || !id) {
    return sendJson(res, 400, { error: 'id is required' });
  }
  if (typeof text !== 'string' || text.length === 0) {
    return sendJson(res, 400, { error: 'text is required' });
  }
  try {
    await sendWithTimeout(jid, { text, edit: { id, fromMe: true, remoteJid: jid } });
    log(`edit ok jid=${redactJid(jid)} id=${id}`);
    sendJson(res, 200, { ok: true });
  } catch (err) {
    sendJson(res, 500, { error: String(err?.message || err) });
  }
}

async function handleReact(req, res) {
  if (!requireSocket(res)) return;
  const body = await readJsonBody(req);
  const { jid, message_id: messageId, emoji, remove } = body || {};
  if (!validJidOr400(res, jid)) return;
  if (typeof messageId !== 'string' || !messageId) {
    return sendJson(res, 400, { error: 'message_id is required' });
  }
  if (!remove && (typeof emoji !== 'string' || emoji.length === 0)) {
    return sendJson(res, 400, { error: 'emoji is required (or set remove: true)' });
  }
  const cached = recentMessages.get(messageId);
  if (!cached) {
    return sendJson(res, 404, { error: 'unknown_message' });
  }
  try {
    await sendWithTimeout(jid, { react: { text: remove ? '' : emoji, key: cached.key } });
    log(`react ok jid=${redactJid(jid)} id=${messageId} remove=${!!remove}`);
    sendJson(res, 200, { ok: true });
  } catch (err) {
    sendJson(res, 500, { error: String(err?.message || err) });
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://bridge');
  const route = `${req.method} ${url.pathname}`;
  if (!authorizedBridgeRequest(
    req.method, url.pathname, req.headers.authorization, BRIDGE_TOKEN,
  )) {
    return sendJson(res, 401, { error: 'unauthorized' });
  }
  try {
    switch (route) {
      case 'GET /health': return handleHealth(req, res);
      case 'GET /events': return handleEvents(req, res);
      case 'GET /self': return handleSelf(req, res);
      case 'POST /send': return await handleSend(req, res);
      case 'POST /send-media': return await handleSendMedia(req, res);
      case 'POST /edit': return await handleEdit(req, res);
      case 'POST /react': return await handleReact(req, res);
      default:
        return sendJson(res, 404, { error: 'not_found' });
    }
  } catch (err) {
    log(`request error ${route}: ${err?.message || err}`);
    if (!res.headersSent) {
      const status = err instanceof SyntaxError ? 400 : (err?.message === 'body too large' ? 413 : 500);
      sendJson(res, status, { error: status === 400 ? 'invalid json body' : String(err?.message || err) });
    } else {
      res.end();
    }
  }
});

// -- Boot ----------------------------------------------------------------------

server.listen(PORT, '0.0.0.0', () => {
  log(`listening on 0.0.0.0:${PORT} (session: ${SESSION_DIR})`);
  if (!paired && !PAIR_MODE) {
    log('NOT PAIRED — no creds.json in session dir. HTTP surface is up; pair with: node bridge.js --pair --phone <digits>');
  }
});

if (PAIR_MODE) {
  log(`pairing mode for +${PAIR_PHONE}`);
  startSocket().catch((err) => {
    log(`pairing failed: ${err?.message || err}`);
    process.exit(1);
  });
} else if (paired) {
  scheduleReconnect.scheduleReconnect();
}
// Unpaired normal boot: serve HTTP with paired:false and wait for the
// operator to run the pairing flow; do not open a socket that would only
// emit QRs nobody is watching.
