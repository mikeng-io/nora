#!/usr/bin/env node
/**
 * Nora WhatsApp Bridge
 *
 * Node 24 daemon that connects to WhatsApp via Baileys and exposes two
 * surfaces:
 *
 *  - LEGACY (design/whatsapp-adoption/03-transport-contract.md): HTTP + SSE
 *    for the pre-streams app. Kept byte-compatible while the migration runs;
 *    it is not the correctness path.
 *  - DURABLE (design/platform-bridges, frozen nora-bridge-wire v1): every
 *    inbound transition is normalized once, fsync-journaled, dedupe-
 *    enqueued to `nq:in:whatsapp:{account_key}` and may earn a queued
 *    receipt; outbound effects go through the reserved-action API
 *    `POST /v1/actions` backed by Postgres `bridge_action`.
 *
 * Durable mode engages when `NORA_BRIDGE_JOURNAL` is set. It requires
 * `NORA_REDIS_URL` and `NORA_BRIDGE_CORRELATION_KEY` (+`_VERSION`); without
 * `DATABASE_URL` the action API answers 503 rather than execute an
 * unreserved effect.
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
 *   POST /send        - { jid, text }                         (legacy)
 *   POST /send-media  - { jid, media_type, data_base64, ... } (legacy)
 *   POST /edit        - { jid, id, text }                     (legacy)
 *   POST /react       - { jid, message_id, emoji, remove? }   (legacy)
 *   GET  /metrics     - Prometheus counters (bearer required)
 *   POST /v1/actions                - reserved durable action
 *   GET  /v1/actions/{action_id}    - recorded action state (readback)
 *   GET  /v1/self                   - {platform, primary, aliases[]}
 *   GET  /v1/health                 - {ok, detail, degraded}
 *   POST /v1/reads                  - {op:'roster', params:{conversation_id}}
 *   PUT|GET /v1/attachments/{key}   - custody bytes
 *
 * Usage:
 *   node bridge.js --port 18081 --session /app/session
 *   node bridge.js --pair --phone 85290000100 --session /app/session
 */

import http from 'node:http';
import path from 'node:path';
import { existsSync } from 'node:fs';
import { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, proto, downloadMediaMessage } from '@whiskeysockets/baileys';
import { createClient } from 'redis';
import pg from 'pg';

import { readBody, sendJson } from './http_util.js';
import { Journal, JournalError } from './journal.js';
import { StreamProducer, streamName } from './queue.js';
import { Projection } from './projection.js';
import { AttachmentStore } from './media.js';
import { ActionStore, receiptIntentIds } from './store.js';
import { createV1, runAction } from './actions.js';
import { initTracing, otlpEndpoint } from './tracing.js';
import { createPipeline } from './pipeline.js';
import { Metrics, jlog } from './obs.js';
import { conversationIdFor, lidForm, phoneForm, senderForm } from './wa_jid.js';

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
  isGroupJid,
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

const PORT = parseInt(getArg('port', process.env.NORA_BRIDGE_PORT || '18081'), 10);
const SESSION_DIR = path.resolve(getArg('session', path.join(process.env.HOME || '~', '.nora', 'whatsapp', 'session')));
const PAIR_MODE = args.includes('--pair');
const PAIR_PHONE = String(getArg('phone', '')).replace(/\D/g, '');
// Shared contract name first; the deployed NORA_BRIDGE_TOKEN stays accepted
// until the root secret projection is renamed with lead coordination.
const BRIDGE_TOKEN = process.env.NORA_BRIDGE_API_TOKEN || process.env.NORA_BRIDGE_TOKEN || '';

// -- Durable-mode env (frozen nora-bridge-wire v1 §env) ----------------------
const JOURNAL_PATH = process.env.NORA_BRIDGE_JOURNAL || '';
const DURABLE = Boolean(JOURNAL_PATH) && !PAIR_MODE;
const REDIS_URL = process.env.NORA_REDIS_URL || '';
const CORRELATION_KEY_HEX = process.env.NORA_BRIDGE_CORRELATION_KEY || '';
const KEY_VERSION = parseInt(process.env.NORA_BRIDGE_CORRELATION_KEY_VERSION || '1', 10) || 1;
// Required, opaque: the stream/account leg the app's account_key must match.
// Never derived from a platform id, never a silent 'default' — a bridge that
// guesses its account leg publishes to a stream nobody consumes.
const ACCOUNT_KEY = process.env.NORA_BRIDGE_ACCOUNT_KEY || '';
const PROJECTION_DIR = process.env.NORA_BRIDGE_PROJECTION_DIR || '/bridge-projection';
const ATTACH_DIR = process.env.NORA_BRIDGE_ATTACH_DIR
  || (JOURNAL_PATH ? path.join(path.dirname(JOURNAL_PATH), 'attachments') : '');
// DATABASE_URL is the canonical name (frozen env contract); NORA_DATABASE_URL
// is the deployed legacy fallback, dropped once root projections rename.
const DATABASE_URL = process.env.DATABASE_URL || process.env.NORA_DATABASE_URL || '';

if (PAIR_MODE && !PAIR_PHONE) {
  console.error('[bridge] --pair requires --phone <digits> (international format, no +)');
  process.exit(2);
}
if (!PAIR_MODE && !BRIDGE_TOKEN) {
  console.error('[bridge] NORA_BRIDGE_API_TOKEN is not set; refusing to start an unauthenticated bridge');
  process.exit(2);
}
if (DURABLE) {
  // A half-configured durable bridge is a lie about durability — refuse to
  // boot rather than journal events that can never reach the stream.
  if (!REDIS_URL) {
    console.error('[bridge] NORA_BRIDGE_JOURNAL is set but NORA_REDIS_URL is not; refusing to start');
    process.exit(2);
  }
  if (!/^[0-9a-fA-F]+$/.test(CORRELATION_KEY_HEX) || CORRELATION_KEY_HEX.length < 32) {
    console.error('[bridge] NORA_BRIDGE_CORRELATION_KEY must be a hex HMAC key (>=16 bytes); refusing to start');
    process.exit(2);
  }
  if (!ACCOUNT_KEY) {
    console.error('[bridge] NORA_BRIDGE_ACCOUNT_KEY is required in durable mode (opaque account leg); refusing to start');
    process.exit(2);
  }
  if (!DATABASE_URL) {
    console.error('[bridge] WARNING: DATABASE_URL unset — /v1/actions will answer 503 (no durable action reservation)');
  }
} else if (!PAIR_MODE) {
  console.log('[bridge] NORA_BRIDGE_JOURNAL unset — legacy SSE mode (no durable capture)');
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

// -- Durable capture objects (null in legacy mode) ----------------------------

const metrics = new Metrics();
let journal = null;
let projection = null;
let attachments = null;
let actionStore = null;
let pipeline = null;
let redisClient = null;
let redisUp = false;
let pgPool = null;
// One serial chain for every journal/enqueue/compaction step — a capture's
// append and a replay drain can never interleave out of order.
let captureChain = Promise.resolve();
function onChain(fn) {
  const task = captureChain.then(fn, fn);
  captureChain = task.catch(() => {});
  return task;
}

function selfForms() {
  const me = sock?.user || {};
  const jid = normalizeJid(me.id);
  const lidJid = normalizeJid(me.lid);
  return {
    phone: jid ? jidUserDigits(jid) || null : null,
    lid: lidJid ? jidUserDigits(lidJid) || null : null,
  };
}

function selfAliases() {
  const s = selfForms();
  return new Set([s.phone && phoneForm(s.phone), s.lid && lidForm(s.lid)].filter(Boolean));
}

// OTel producer/attempt/custody spans; null when no exporter endpoint is
// configured — the callsites then no-op. A batch exporter means an OTLP
// outage drops spans, never events.
const tracer = initTracing();
if (DURABLE && tracer) {
  console.log(`[bridge] otel spans → ${otlpEndpoint().replace(/\/v1\/traces.*$/, '/v1/traces')}`);
}

// The executor context shared by /v1/actions and the queued-receipt step.
const actionCtx = {
  get sock() { return sock; },
  connected: () => Boolean(sock && connected),
  sendWithTimeout,
  attachments: null,          // filled by initDurable
  recentMessages,
  get selfAliases() { return selfAliases(); },
};

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
          if (pipeline) {
            onChain(() => pipeline.noteAmbiguous(ambiguousFor(msg, opened))).catch(captureDied);
          }
          continue;
        }
        log(`secret edit decrypted id=${msg.key.id} chat=${redactJid(msg.key.remoteJid)} shape=[${opened.shape}]`);
        source = opened.msg;
      }
      const event = extractMessageEvent(source, { lidToPhone, phoneToLid, recentCache: recentMessages, self: selfForms() });
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
      if (pipeline) {
        // The durable path owns admission → custody → journal → enqueue →
        // receipt and emits the legacy SSE event inside, for compat.
        onChain(() => pipeline.capture(event, source)).catch(captureDied);
      } else {
        emitEvent(event);
      }
    }
  });
}

/**
 * Identity fields for an undecryptable secret edit, resolved from the OUTER
 * stanza (the only thing readable) into the same normalized forms a captured
 * event would carry — the admission check and the ambiguous record need them.
 */
function ambiguousFor(msg, opened) {
  const chatJid = normalizeJid(msg.key.remoteJid) || msg.key.remoteJid;
  const group = isGroupJid(chatJid);
  const senderJid = normalizeJid(group && msg.key.participant ? msg.key.participant : msg.key.remoteJid) || '';
  const id = resolveIdentity(senderJid, lidToPhone, phoneToLid);
  return {
    conversationId: conversationIdFor(chatJid, id.phone),
    conversationType: group ? 'group' : 'dm',
    senderId: senderForm(senderJid, id.phone, id.lid),
    stanzaId: msg.key.id || null,
    targetId: getMessageContent(msg)?.secretEncryptedMessage?.targetMessageKey?.id || null,
    reason: opened?.reason || 'undecryptable',
  };
}

/**
 * A JournalError anywhere in the capture path means durable capture is
 * broken: stop upstream (close the socket) and exit — the supervisor's
 * restart re-runs the journal preflight, which keeps the bridge down while
 * the volume stays broken. Never drop the oldest, never keep receiving.
 */
function captureDied(err) {
  if (err instanceof JournalError) {
    log(`CAPTURE JOURNAL BROKEN — stopping upstream and exiting: ${err.message}`);
    jlog('capture.halt', { reason: 'journal_broken' });
    try { sock?.end(); } catch { try { sock?.ws?.close(); } catch { /* already gone */ } }
    setTimeout(() => process.exit(1), 300).unref();
    return;
  }
  log(`capture pipeline error: ${err?.message || err}`);
}

// -- Durable bootstrap ---------------------------------------------------------

async function initDurable() {
  // Journal preflight BEFORE upstream receive — the contract's first step,
  // on every restart, not once.
  journal = new Journal(JOURNAL_PATH);
  try {
    journal.probe();
  } catch (err) {
    console.error(`[bridge] journal preflight failed (${err?.message || err}); refusing upstream`);
    process.exit(2);
  }
  attachments = new AttachmentStore(ATTACH_DIR);
  actionCtx.attachments = attachments;
  projection = new Projection(PROJECTION_DIR, 'whatsapp', { log });
  projection.start();

  pgPool = DATABASE_URL ? new pg.Pool({ connectionString: DATABASE_URL, max: 4 }) : null;
  actionStore = pgPool ? new ActionStore(pgPool, {
    platform: 'whatsapp', accountKey: ACCOUNT_KEY,
    // The producer evidence an `event_receipt` owner must name — recomputed
    // per call so intents captured after boot count (never a stale snapshot).
    receiptIntents: () => receiptIntentIds(journal, {
      correlationKey: Buffer.from(CORRELATION_KEY_HEX, 'hex'),
      keyVersion: KEY_VERSION,
    }),
  }) : null;

  redisClient = createClient({
    url: REDIS_URL,
    socket: { reconnectStrategy: (retries) => Math.min(1000 * 2 ** Math.min(retries, 5), 30000) },
    // Offline commands reject immediately — a queued XADD that flushes later
    // would sit ahead of newer captures on the chain; the journal + dedupe
    // make rejection the cheaper, idempotent path.
    disableOfflineQueue: true,
  });
  redisClient.on('ready', () => {
    redisUp = true;
    log('redis connected — draining journal');
    if (pipeline) pipeline.pump()?.catch(captureDied);
  });
  redisClient.on('end', () => { redisUp = false; });
  redisClient.on('error', (err) => { redisUp = false; log(`redis error: ${err?.message || err}`); });
  try {
    await redisClient.connect();
  } catch (err) {
    redisUp = false;
    log(`redis unavailable at boot (${err?.message || err}) — capture journals until it returns`);
  }

  const producer = new StreamProducer(redisClient, streamName('whatsapp', ACCOUNT_KEY));
  pipeline = createPipeline({
    platform: 'whatsapp', accountKey: ACCOUNT_KEY,
    correlationKey: Buffer.from(CORRELATION_KEY_HEX, 'hex'), keyVersion: KEY_VERSION,
    journal, producer, projection, attachments,
    downloadMedia: async (rawMsg) => {
      if (!sock || !connected) throw new Error('not_connected');
      return downloadMediaMessage(rawMsg, 'buffer', {},
        { logger: baileysLogger, reuploadRequest: sock.updateMediaMessage });
    },
    runReceipt: async ({ event_id, request, traceLinks }) => runAction({
      store: actionStore, ctx: actionCtx,
      actionId: `${event_id}:react:0`,
      owner: { kind: 'event_receipt', id: event_id, generation: null },
      op: 'react', request, metrics, tracer, traceLinks,
    }),
    emitLegacy: emitEvent,
    metrics, jlog, tracer,
  });

  if (redisUp) {
    await onChain(() => pipeline.replay());
  }
  // Periodic drain — a Redis flap or a mid-run outage leaves the journal
  // pending; the head-first drain publishes it back in order.
  const drainTimer = setInterval(() => {
    if (redisUp && pipeline) pipeline.pump()?.catch(captureDied);
  }, 60000);
  drainTimer.unref();
}

// -- HTTP surface -------------------------------------------------------------

const MAX_BODY_BYTES = 100 * 1024 * 1024;

async function readJsonBody(req) {
  const raw = await readBody(req, MAX_BODY_BYTES);
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
function sendWithTimeout(jid, payload, opts = {}) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('sendMessage timed out after 60s')), SEND_TIMEOUT_MS);
  });
  return enqueueSend(() =>
    Promise.race([sock.sendMessage(jid, payload, opts), timeout]).finally(() => clearTimeout(timer))
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
    sendJson(res, 500, { error: 'bridge operation failed' });
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
    sendJson(res, 500, { error: 'bridge operation failed' });
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
    sendJson(res, 500, { error: 'bridge operation failed' });
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
    sendJson(res, 500, { error: 'bridge operation failed' });
  }
}

function healthV1() {
  const journalBroken = Boolean(pipeline?.broken);
  const projMissing = projection ? !projection.valid : false;
  const projStale = Boolean(projection?.stale);
  const degraded = Boolean(!redisUp || projMissing || projStale || journalBroken);
  const ok = Boolean(sock && connected && paired && !loggedOut) && !journalBroken;
  const detail = journalBroken ? 'journal_broken'
    : !redisUp ? 'redis_down'
    : projMissing ? 'projection_missing'
    : projStale ? 'projection_stale' : null;
  return { ok, detail, degraded, connected, paired };
}

const handleV1 = createV1({
  get store() { return actionStore; },
  ctx: actionCtx,
  tracer,
  get attachments() { return attachments; },
  get sock() { return sock; },
  self: () => {
    if (!sock || !connected) return null;
    const s = selfForms();
    const aliases = [s.phone && phoneForm(s.phone), s.lid && lidForm(s.lid)].filter(Boolean);
    return {
      platform: 'whatsapp',
      primary: aliases[0] || null,
      aliases,
      phone: s.phone,
      lid: s.lid,
      jid: normalizeJid(sock.user?.id) || null,
      lid_jid: normalizeJid(sock.user?.lid) || null,
    };
  },
  health: healthV1,
  metrics,
});

const server = http.createServer(async (req, res) => {
  let route = `${req.method} ${req.url}`;
  try {
    // The guard is inside this try too: a malformed request must answer
    // (401/400), never reject the handler and take the bridge down.
    const url = new URL(req.url, 'http://bridge');
    route = `${req.method} ${url.pathname}`;
    if (!authorizedBridgeRequest(
      req.method, url.pathname, req.headers.authorization, BRIDGE_TOKEN,
    )) {
      return sendJson(res, 401, { error: 'unauthorized' });
    }
    if (DURABLE && url.pathname.startsWith('/v1/') && await handleV1(req, res, url)) return;
    switch (route) {
      case 'GET /health': return handleHealth(req, res);
      case 'GET /events': return handleEvents(req, res);
      case 'GET /self': return handleSelf(req, res);
      case 'GET /metrics': {
        res.writeHead(200, { 'content-type': 'text/plain; version=0.0.4' });
        return res.end(metrics.text());
      }
      case 'POST /send': return await handleSend(req, res);
      case 'POST /send-media': return await handleSendMedia(req, res);
      case 'POST /edit': return await handleEdit(req, res);
      case 'POST /react': return await handleReact(req, res);
      default:
        return sendJson(res, 404, { error: 'not_found' });
    }
  } catch (err) {
    log('request failed');
    if (!res.headersSent) {
      const status = err instanceof SyntaxError ? 400 : (err?.message === 'body too large' ? 413 : 500);
      sendJson(res, status, { error: status === 400 ? 'invalid json body'
        : status === 413 ? 'body too large' : 'bridge operation failed' });
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

// Durable bootstrap BEFORE upstream receive: journal probe → custody dir →
// projection → Redis → journal replay, all ahead of the first stanza.
if (DURABLE) {
  await initDurable();
}

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
