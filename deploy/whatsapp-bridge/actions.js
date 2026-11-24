/**
 * The `platform` contract this bridge serves (design/service-api/, the routes
 * in docs/service-api/openapi.json): delivery operations, queries, the account
 * read, blob custody and the `operations` status read, all behind the shared
 * bearer, plus the root probes `/health` and `/ready`. Every answer on the two
 * prefixes is written by `contract.js`.
 *
 * Action truth: `confirmed` means Baileys' sendMessage RESOLVED with a stanza —
 * an HTTP-200-shaped answer, not a guess. `rejected` means the op was refused
 * before any upstream bytes (validation, fence, media custody) — definitely
 * not submitted. `uncertain` means the upstream call may have gone out and the
 * result could not be determined — never resolved to success, never retried
 * blindly. Those stored states become outcomes only at the edge.
 */
import { ACTION_OPS, OWNER_KINDS, actionResponse } from './store.js';
import { isValidTargetJid, isGroupJid, isLidJid, jidUserDigits, normalizeJid, buildMediaPayload, timestampMs } from './bridge_helpers.js';
import { lidForm, phoneForm } from './wa_jid.js';
import { validAttachmentKey } from './media.js';
import { readBody, readBoundedBody, sendJson } from './http_util.js';
import {
  operationEnvelope, readEnvelope, scopeOf, sendOperation, sendRead, sendRefusal,
  settledOperation, traceOf,
} from './contract.js';

export class ActionRejected extends Error {
  constructor(kind, message) { super(message); this.kind = kind; }
}

/** External error details never enter action results or HTTP responses. */
export function sanitizeError(_err, kind = 'transport') {
  return { kind, message: 'bridge operation failed' };
}

function targetJid(request) {
  const t = request?.target;
  if (!t || typeof t !== 'object') throw new ActionRejected('bad_request', 'missing target');
  const { kind, id } = t;
  if (typeof id !== 'string' || !isValidTargetJid(id)) {
    throw new ActionRejected('bad_request', 'invalid target id');
  }
  if (kind === 'group' && !isGroupJid(id)) {
    throw new ActionRejected('bad_request', 'target.kind=group but id is not a group JID');
  }
  if (kind === 'dm' && (isGroupJid(id) || jidUserDigits(id) === '')) {
    throw new ActionRejected('bad_request', 'target.kind=dm but id is not a person JID');
  }
  return id;
}

/** The mention's platform address: `"+852…"` → phone JID, bare digits → LID. */
export function recipientToJid(recipient) {
  const s = String(recipient || '');
  if (!s) return null;
  if (s.includes('@')) return s;
  if (s.startsWith('+')) {
    const digits = s.slice(1);
    return /^\d+$/.test(digits) ? `${digits}@s.whatsapp.net` : null;
  }
  const digits = lidForm(s);
  return digits ? `${digits}@lid` : null;
}

function mentionJids(mentions) {
  const out = [];
  for (const m of Array.isArray(mentions) ? mentions : []) {
    const jid = m && recipientToJid(m.recipient);
    if (jid && !out.includes(jid)) out.push(jid);
  }
  return out;
}

/**
 * Op executors. `ctx`: { sock, sendWithTimeout, attachments, recentMessages,
 * self {phone,lid} }. Validation throws ActionRejected BEFORE the wire;
 * anything thrown after sendMessage is in flight is the caller's `uncertain`.
 */
export const executors = {
  async send(request, ctx) {
    const jid = targetJid(request);
    const atts = Array.isArray(request.attachments) ? request.attachments : [];
    const text = typeof request.text === 'string' ? request.text : '';
    const mentions = mentionJids(request.mentions);
    if (!text && atts.length === 0) {
      throw new ActionRejected('bad_request', 'send needs text or attachments');
    }
    const payloads = [];
    let captionUsed = false;
    for (const a of atts) {
      const key = a && a.key;
      const bytes = key ? ctx.attachments.read(key) : null;
      if (!bytes) throw new ActionRejected('attachment', `custody key absent: ${String(key).slice(0, 64)}`);
      const top = String(a.media_type || '').split(';', 1)[0].split('/', 1)[0].toLowerCase();
      const mediaType = { image: 'image', video: 'video', audio: 'audio' }[top] || 'document';
      // The caption goes on the FIRST payload that can carry it — one copy
      // of the text, never repeated across every attachment stanza.
      const caption = !captionUsed && mediaType !== 'audio' && text ? text : undefined;
      if (caption) captionUsed = true;
      const payload = buildMediaPayload({
        mediaType, buffer: bytes,
        filename: typeof a.file_name === 'string' ? a.file_name : undefined,
        caption, mimetype: typeof a.media_type === 'string' ? a.media_type : undefined,
      });
      if (!payload) throw new ActionRejected('bad_request', `unmappable media_type ${a.media_type}`);
      payloads.push(payload);
    }
    if (text && !captionUsed && payloads.length) {
      // Every attachment payload was captionless (audio/sticker) — the text
      // goes out as its own message first, the same thing a person would do.
      payloads.unshift({ text });
    } else if (payloads.length === 0) {
      payloads.push({ text });
    }
    if (mentions.length) {
      for (const p of payloads) p.mentions = mentions;
    }
    // A reply is best-effort: attach the quoted stub only while the parent's
    // stanza is still in the recent-message cache.
    const quotedId = request.quote && typeof request.quote === 'object'
      ? request.quote.platform_message_id : null;
    const cached = quotedId && ctx.recentMessages ? ctx.recentMessages.get(quotedId) : null;
    const opts = cached?.raw ? { quoted: cached.raw } : {};

    const refs = [];
    let last = null;
    for (const payload of payloads) {
      last = await ctx.sendWithTimeout(jid, payload, opts);
      if (last?.key?.id) {
        refs.push(String(last.key.id));
        if (ctx.recentMessages) ctx.recentMessages.remember(last);
      }
    }
    return { platform_ref: last?.key?.id || null, platform_refs: refs,
      platform_ms: timestampMs(last?.messageTimestamp) };
  },

  async react(request, ctx) {
    const jid = targetJid(request);
    const toMessageId = String(request.to_message_id || '');
    if (!toMessageId) throw new ActionRejected('bad_request', 'react needs to_message_id');
    const remove = Boolean(request.remove);
    const emoji = typeof request.emoji === 'string' ? request.emoji : '';
    if (!remove && !emoji) throw new ActionRejected('bad_request', 'react needs emoji or remove:true');

    const cached = ctx.recentMessages ? ctx.recentMessages.get(toMessageId) : null;
    let key = cached?.key || null;
    if (!key) {
      const author = String(request.to_author || '');
      const fromMe = author !== '' && ctx.selfAliases.has(author);
      key = { remoteJid: jid, id: toMessageId, fromMe };
      if (!fromMe && isGroupJid(jid) && author) {
        key.participant = recipientToJid(author);
      }
    }
    const sent = await ctx.sendWithTimeout(jid, { react: { text: remove ? '' : emoji, key } });
    return { platform_ref: sent?.key?.id || toMessageId, platform_ms: timestampMs(sent?.messageTimestamp) };
  },

  async edit(request, ctx) {
    const jid = targetJid(request);
    const ref = String(request.message_ref || '');
    const text = typeof request.text === 'string' ? request.text : '';
    if (!ref) throw new ActionRejected('bad_request', 'edit needs message_ref');
    if (!text) throw new ActionRejected('bad_request', 'edit needs text');
    const sent = await ctx.sendWithTimeout(jid, { text, edit: { id: ref, fromMe: true, remoteJid: jid } });
    return { platform_ref: sent?.key?.id || ref, platform_ms: timestampMs(sent?.messageTimestamp) };
  },

  async typing(request, ctx) {
    const jid = targetJid(request);
    await ctx.sock.sendPresenceUpdate('composing', jid);
    return { platform_ref: null, platform_ms: null };
  },
};

/** The closed query kinds of the platform contract; this bridge implements `roster`. */
export const QUERY_KINDS = new Set([
  'roster', 'members', 'channels', 'channel', 'member_presence', 'voice_presence', 'groups', 'music_status',
]);

export function selfForms(user) {
  const jid = normalizeJid(user?.id);
  const lidJid = normalizeJid(user?.lid);
  return {
    phone: jid ? jidUserDigits(jid) || null : null,
    lid: lidJid ? jidUserDigits(lidJid) || null : null,
  };
}

export function buildAccount(user) {
  const { phone, lid } = selfForms(user);
  const aliases = [phone && phoneForm(phone), lid && lidForm(lid)].filter(Boolean);
  if (!aliases.length) return null;
  return {
    platform: 'whatsapp',
    primary: aliases[0],
    aliases,
    capabilities: [],
    phone,
    lid,
    jid: normalizeJid(user?.id) || null,
    lid_jid: normalizeJid(user?.lid) || null,
  };
}

/**
 * The action pipeline shared by `POST /v1/platform/operations` and the
 * receipt step: reserve → (fence) → execute → record → result. `reserve`
 * answers replay and conflict before any upstream call.
 */
export async function runAction({ store, ctx, actionId, owner, op, request, metrics, tracer, traceLinks = [] }) {
  const span = tracer?.startSpan('bridge.action.attempt', {
    kind: 'producer',
    links: traceLinks,
    attributes: { 'nora.trace_kind': 'queue', 'bridge.op': op, 'bridge.owner_kind': owner?.kind || 'unknown' },
  });
  const out = await runActionInner({ store, ctx, actionId, owner, op, request, metrics });
  span?.setAttribute?.('bridge.action.state', out?.state || 'unavailable');
  span?.end?.(out?.state === 'uncertain' ? 'uncertain' : null);
  return out;
}

function unavailable(actionId, kind, message) {
  return { action_id: actionId, state: 'unavailable', platform_ref: null, platform_refs: null,
    platform_ms: null, message_id: null, error: { kind, message } };
}

async function runActionInner({ store, ctx, actionId, owner, op, request, metrics }) {
  if (!store) return unavailable(actionId, 'store_unavailable', 'bridge_action store not configured');
  if (ctx.connected && !ctx.connected()) {
    // Definitely not submitted — answered BEFORE reserving so no 'started'
    // row pretends the upstream call may have happened. The caller treats
    // this as transport-unavailable (retryable upstream), not a rejection.
    return unavailable(actionId, 'not_connected', 'upstream socket is not connected');
  }
  let verdict;
  try {
    verdict = await store.reserve({ actionId, owner, op, request });
  } catch (err) {
    // The reservation never landed — nothing ran, safe to say unavailable.
    metrics?.inc('bridge_action_total', { op, state: 'unavailable' });
    return unavailable(actionId, 'store_unavailable', sanitizeError(err, 'store').message);
  }
  if (verdict.verdict === 'conflict') {
    metrics?.inc('bridge_action_total', { op, state: 'conflict' });
    return { action_id: actionId, state: 'rejected', conflict: true,
      platform_ref: null, platform_refs: null, platform_ms: null, message_id: null,
      error: { kind: 'conflict', message: 'action_id reused with a different request' } };
  }
  if (verdict.verdict === 'replay') {
    // Covers both a previously recorded result and a row the in-transaction
    // owner fence just rejected — returned verbatim, never re-executed.
    if (verdict.row?.state === 'rejected' && verdict.row?.error?.kind === 'fenced') {
      metrics?.inc('bridge_action_total', { op, state: 'fenced' });
    }
    return actionResponse(verdict.row);
  }
  // verdict 'execute' — the row is 'started' and owner authorization already
  // happened inside the reservation transaction.
  const startedAt = Date.now();
  const messageId = verdict.messageId ?? null;
  const settled = (state, fields) => ({ action_id: actionId, state, message_id: messageId,
    platform_ref: null, platform_refs: null, platform_ms: null, error: null,
    started_at: startedAt, completed_at: Date.now(), ...fields });
  let state;
  let fields;
  try {
    const out = await executors[op](request, ctx);
    state = 'confirmed';
    fields = { platform_ref: out.platform_ref, platform_refs: out.platform_refs ?? null,
      platform_ms: out.platform_ms };
  } catch (err) {
    state = err instanceof ActionRejected ? 'rejected' : 'uncertain';
    fields = { error: err instanceof ActionRejected
      ? { kind: err.kind, message: sanitizeError(err).message } : sanitizeError(err) };
  }
  // The row is `started`: a terminal write that does not land leaves the
  // outcome unrecorded, which is `uncertain` — never `unavailable`, whose
  // meaning is "not attempted".
  for (const [s, f] of [[state, fields], ['uncertain', { error: sanitizeError(null, 'ledger') }]]) {
    try {
      await store.finish(actionId, s, { platformRef: f.platform_ref ?? null,
        platformMs: f.platform_ms ?? null, platformRefs: f.platform_refs ?? null, error: f.error ?? null });
      metrics?.inc('bridge_action_total', { op, state: s });
      return settled(s, f);
    } catch { /* the next attempt records uncertainty */ }
  }
  metrics?.inc('bridge_action_total', { op, state: 'uncertain' });
  return settled('uncertain', { error: sanitizeError(null, 'ledger') });
}

const isObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v);

async function jsonBody(req) {
  return JSON.parse((await readBody(req)).toString('utf8') || '{}');
}

function deliveryOwnerFault(owner) {
  if (!isObject(owner) || !OWNER_KINDS.has(owner.kind) || typeof owner.id !== 'string' || !owner.id) {
    return 'owner.kind must be work|event_receipt|otp_challenge|live_invocation with an id';
  }
  if (owner.kind === 'work' && !Number.isInteger(owner.generation)) {
    return 'work owner requires integer generation';
  }
  return null;
}

/**
 * The contract's routes. `deps`: { store, ctx, attachments, self, health,
 * liveness, ready, metrics, tracer } — `self`, `health`, `liveness` and
 * `ready` are callables read per request.
 */
function routeTable(deps) {
  const healthy = () => Boolean(deps.health?.()?.ok);

  /** The bridge's status, `ok` only when it can also deliver; `/ready` agrees. */
  function readiness() {
    const status = { connected_at_ms: null, ...deps.health?.() };
    const missing = !deps.store ? 'action_store_missing'
      : !deps.attachments ? 'custody_missing'
        : !deps.self?.() ? 'no_account_identity'
          : deps.ready && !deps.ready() ? 'not_ready' : null;
    if (missing && status.ok) Object.assign(status, { ok: false, detail: missing });
    return { ...status, ok: Boolean(status.ok) };
  }

  async function submitOperation(req, res, trace) {
    const body = await jsonBody(req);
    const { operation_id: actionId, owner, kind: op, effect } = body || {};
    if (typeof actionId !== 'string' || !actionId) return sendRefusal(res, 400, 'operation_id required');
    if (typeof op !== 'string' || !ACTION_OPS.has(op)) return sendRefusal(res, 422, 'unknown operation kind');
    const music = op.startsWith('music_');
    if (!music) {
      const fault = deliveryOwnerFault(owner);
      if (fault) return sendRefusal(res, 400, fault);
    }
    if (!isObject(effect)) return sendRefusal(res, 400, 'effect must be an object');
    if (music) {
      const target = typeof effect.conversation_id === 'string' ? effect.conversation_id : '';
      return sendOperation(res, settledOperation(trace, {
        outcome: 'unsupported', target, scope: scopeOf(target),
        payload: { error: { kind: 'unsupported', message: 'music is not supported' } },
      }));
    }
    const target = typeof effect.target?.id === 'string' ? effect.target.id : '';
    const out = await runAction({ store: deps.store, ctx: deps.ctx,
      actionId, owner: { kind: owner.kind, id: owner.id, generation: owner.generation ?? null },
      op, request: effect, metrics: deps.metrics, tracer: deps.tracer,
      // The caller's W3C context — a span link, never a parent, and
      // absent/invalid headers simply carry no link.
      traceLinks: req.headers?.['traceparent'] ? [String(req.headers['traceparent'])] : [] });
    if (out.conflict) return sendRefusal(res, 409, 'operation_id reused for another effect');
    return sendOperation(res, operationEnvelope(trace, out, target));
  }

  async function readOperation(req, res, trace, [id]) {
    const read = (outcome, payload = null) => sendRead(res, readEnvelope(trace,
      { outcome, payload, healthy: healthy() }));
    if (!deps.store) return read('unavailable');
    let row;
    try { row = await deps.store.get(id); } catch { return read('unavailable'); }
    if (!row) return read('nothing_found');
    const target = typeof row.request?.target?.id === 'string' ? row.request.target.id : '';
    const env = operationEnvelope(trace, actionResponse(row), target);
    return sendRead(res, readEnvelope(trace, { outcome: 'ok', payload: env,
      healthy: healthy(), scope: env.scope }));
  }

  async function query(req, res, trace) {
    const body = await jsonBody(req);
    if (!isObject(body) || typeof body.kind !== 'string' || !QUERY_KINDS.has(body.kind)) {
      return sendRefusal(res, 422, 'unknown query kind');
    }
    const params = isObject(body.params) ? body.params : {};
    const jid = params.conversation_id;
    const scope = scopeOf(typeof jid === 'string' ? jid : null);
    const read = (outcome, payload = null) => sendRead(res, readEnvelope(trace,
      { outcome, payload, healthy: healthy(), scope }));
    // A closed kind this bridge does not implement is unavailable, never empty.
    if (body.kind !== 'roster') return read('unavailable');
    if (typeof jid !== 'string' || !isGroupJid(jid) || !deps.ctx?.sock || !deps.ctx.connected?.()) {
      return read('unavailable');
    }
    let meta;
    try { meta = await deps.ctx.sock.groupMetadata(jid); } catch { return read('unavailable'); }
    const members = [];
    for (const p of meta?.participants || []) {
      const raw = p.phoneNumber ? `${p.phoneNumber}@s.whatsapp.net` : (p.id || p.lid || '');
      const digits = jidUserDigits(raw);
      if (!digits) continue;
      members.push(isLidJid(raw) ? lidForm(digits) : phoneForm(digits));
    }
    return read('ok', { available: true, members });
  }

  async function readAccount(req, res, trace, [id]) {
    const read = (outcome, payload = null) => sendRead(res, readEnvelope(trace,
      { outcome, payload, healthy: healthy() }));
    if (id !== 'self') return read('nothing_found');
    const me = deps.self();
    return me ? read('ok', { ...me, capabilities: me.capabilities ?? [] }) : read('unavailable');
  }

  async function putBlob(req, res, trace, [key]) {
    const settle = (outcome, payload = null) => sendOperation(res,
      settledOperation(trace, { outcome, payload, target: key }));
    const store = deps.attachments;
    if (!store) return settle('unavailable');
    if (!validAttachmentKey(key)) return sendRefusal(res, 400, 'invalid blob key');
    const { data, over } = await readBoundedBody(req, store.maxBytes);
    const refuse = (kind, message) => settle('rejected', { error: { kind, message } });
    if (over) return refuse('too_large', 'blob over the size bound');
    let result;
    try { result = store.put(key, data); } catch { return settle('unavailable'); }
    if (result === 'conflict') return refuse('conflict', 'blob key already holds different bytes');
    deps.metrics?.inc('bridge_attachment_put_total', { result });
    return settle('complete', { key });
  }

  async function getBlob(req, res, trace, [key]) {
    const read = (outcome) => sendRead(res, readEnvelope(trace,
      { outcome, healthy: healthy() }));
    if (!deps.attachments) return read('unavailable');
    let data;
    try { data = deps.attachments.fetch(key); } catch { return read('unavailable'); }
    if (data === null) return read('nothing_found');
    res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': data.length });
    res.end(data);
    return true;
  }

  async function readStatus(req, res, trace) {
    const status = readiness();
    return sendRead(res, readEnvelope(trace, { outcome: 'ok', payload: status,
      healthy: status.ok }));
  }

  return [
    ['POST', '/v1/platform/operations', 'operation', submitOperation],
    ['GET', '/v1/platform/operations/{operation_id}', 'read', readOperation],
    ['POST', '/v1/platform/queries', 'read', query],
    ['GET', '/v1/platform/accounts/{account_id}', 'read', readAccount],
    ['PUT', '/v1/platform/blobs/{key}', 'operation', putBlob],
    ['GET', '/v1/platform/blobs/{key}', 'read', getBlob],
    ['GET', '/v1/operations/status', 'read', readStatus],
    ['GET', '/health', 'probe', (req, res) => sendJson(res, 200, deps.liveness?.() ?? { ok: true })],
    ['GET', '/ready', 'probe', (req, res) => {
      const ready = readiness().ok;
      return sendJson(res, ready ? 200 : 503, { ready });
    }],
  ].map(([method, path, family, handler]) => ({
    method, path, family, handler,
    pattern: new RegExp(`^${path.replace(/\{[a-z_]+\}/g, '([^/]+)')}$`),
  }));
}

/** The routes this bridge serves, as `METHOD path` with the contract's parameter names. */
export const SERVED_ROUTES = Object.freeze(routeTable({}).map((r) => `${r.method} ${r.path}`));

/**
 * Route the contract. Returns true when it handled the request; false for a
 * path it does not serve (the caller's 404).
 */
export function createV1(deps) {
  const routes = routeTable(deps);
  return async function handleV1(req, res, url) {
    let route = null;
    let match = null;
    for (const r of routes) {
      match = req.method === r.method && r.pattern.exec(url.pathname);
      if (match) { route = r; break; }
    }
    if (!route) return false;
    const trace = traceOf(req);
    try {
      let params;
      try { params = match.slice(1).map(decodeURIComponent); } catch {
        return sendRefusal(res, 400, 'malformed path parameter');
      }
      return await route.handler(req, res, trace, params);
    } catch (err) {
      if (res.headersSent) { res.end(); return true; }
      if (err instanceof SyntaxError) return sendRefusal(res, 400, 'invalid json body');
      // The server's refusal of a JSON body over the cap, before any handler ran.
      if (err?.message === 'body too large') return sendRefusal(res, 413, 'body too large');
      // Anything else is a measurer that could not answer: unavailable, never a 500.
      if (route.family === 'probe') return sendJson(res, 503, { ok: false });
      if (route.family === 'operation') {
        return sendOperation(res, settledOperation(trace, { outcome: 'unavailable',
          payload: { platform_refs: [], platform_ms: null, message_id: null,
            error: { kind: 'unavailable', message: 'bridge operation failed' } } }));
      }
      return sendRead(res, readEnvelope(trace, { outcome: 'unavailable', healthy: false }));
    }
  };
}
