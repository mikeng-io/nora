/**
 * The shared `/v1` bridge API (frozen wire §actions_api): actions, self,
 * health, reads and attachment custody, all behind the shared bearer.
 *
 * Action truth: `confirmed` means Baileys' sendMessage RESOLVED with a stanza —
 * an HTTP-200-shaped answer, not a guess. `rejected` means the op was refused
 * before any upstream bytes (validation, fence, media custody) — definitely
 * not submitted. `uncertain` means the upstream call may have gone out and the
 * result could not be determined — never resolved to success, never retried
 * blindly. `unsupported` means the platform has no such op.
 */
import { ACTION_OPS, OWNER_KINDS, actionResponse } from './store.js';
import { isValidTargetJid, isGroupJid, isLidJid, jidUserDigits, buildMediaPayload, timestampMs } from './bridge_helpers.js';
import { lidForm, phoneForm } from './wa_jid.js';
import { readBody, sendJson } from './http_util.js';

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

    let last = null;
    for (const payload of payloads) {
      last = await ctx.sendWithTimeout(jid, payload, opts);
      if (last?.key?.id && ctx.recentMessages) ctx.recentMessages.remember(last);
    }
    return { platform_ref: last?.key?.id || null, platform_ms: timestampMs(last?.messageTimestamp) };
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

function badRequest(res, message) {
  return sendJson(res, 400, { error: 'bad_request', detail: message });
}

/**
 * The /v1 action pipeline shared by `POST /v1/actions` and the receipt step:
 * reserve → (fence) → execute → record → respond. `reserve` answers replay
 * and conflict before any upstream call.
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

async function runActionInner({ store, ctx, actionId, owner, op, request, metrics }) {
  if (!store) {
    return { action_id: actionId, state: 'unavailable', outcome: null,
      platform_ref: null, platform_ms: null,
      error: { kind: 'store_unavailable', message: 'bridge_action store not configured' }, _status: 503 };
  }
  if (ctx.connected && !ctx.connected()) {
    // Definitely not submitted — answered BEFORE reserving so no 'started'
    // row pretends the upstream call may have happened. The caller treats
    // this as transport-unavailable (retryable upstream), not a rejection.
    return { action_id: actionId, state: 'unavailable', outcome: null,
      platform_ref: null, platform_ms: null,
      error: { kind: 'not_connected', message: 'upstream socket is not connected' }, _status: 503 };
  }
  let verdict;
  try {
    verdict = await store.reserve({ actionId, owner, op, request });
  } catch (err) {
    // The reservation never landed — nothing ran, safe to say unavailable.
    metrics?.inc('bridge_action_total', { op, state: 'unavailable' });
    return { action_id: actionId, state: 'unavailable', outcome: null,
      platform_ref: null, platform_ms: null,
      error: { kind: 'store_unavailable', message: sanitizeError(err, 'store').message }, _status: 503 };
  }
  if (verdict.verdict === 'conflict') {
    metrics?.inc('bridge_action_total', { op, state: 'conflict' });
    return { action_id: actionId, state: 'rejected', outcome: 'rejected',
      platform_ref: null, platform_ms: null,
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
  try {
    const out = await executors[op](request, ctx);
    await store.finish(actionId, 'confirmed', {
      platformRef: out.platform_ref, platformMs: out.platform_ms });
    metrics?.inc('bridge_action_total', { op, state: 'confirmed' });
    return { action_id: actionId, state: 'confirmed', outcome: 'confirmed',
      platform_ref: out.platform_ref, platform_ms: out.platform_ms, error: null };
  } catch (err) {
    if (err instanceof ActionRejected) {
      const error = { kind: err.kind, message: sanitizeError(err).message };
      await store.finish(actionId, 'rejected', { error });
      metrics?.inc('bridge_action_total', { op, state: 'rejected' });
      return { action_id: actionId, state: 'rejected', outcome: 'rejected',
        platform_ref: null, platform_ms: null, error };
    }
    const error = sanitizeError(err);
    await store.finish(actionId, 'uncertain', { error });
    metrics?.inc('bridge_action_total', { op, state: 'uncertain' });
    return { action_id: actionId, state: 'uncertain', outcome: 'uncertain',
      platform_ref: null, platform_ms: null, error };
  }
}

/**
 * Route the /v1 surface. `deps`: { store, ctx, attachments, self, health,
 * metrics, sock } — `self` is a callable returning the identity snapshot,
 * `health` likewise. Returns true when it handled the request.
 */
export function createV1(deps) {
  return async function handleV1(req, res, url) {
    const route = `${req.method} ${url.pathname}`;
    try {
      switch (true) {
        case route === 'POST /v1/actions': {
          const body = JSON.parse((await readBody(req)).toString('utf8') || '{}');
          const { action_id: actionId, owner, op, request } = body || {};
          if (typeof actionId !== 'string' || !actionId) return badRequest(res, 'action_id required');
          if (!owner || typeof owner !== 'object' || !OWNER_KINDS.has(owner.kind)
              || typeof owner.id !== 'string' || !owner.id) {
            return badRequest(res, 'owner.kind must be work|event_receipt|otp_challenge|live_invocation with an id');
          }
          if (owner.kind === 'work' && !Number.isInteger(owner.generation)) {
            return badRequest(res, 'work owner requires integer generation');
          }
          if (typeof op !== 'string' || !ACTION_OPS.has(op) || !executors[op]) {
            return sendJson(res, 200, { action_id: actionId, state: 'rejected',
              outcome: 'unsupported', platform_ref: null, platform_ms: null,
              error: { kind: 'unsupported', message: String(op).slice(0, 40) } });
          }
          if (typeof request !== 'object' || request === null || Array.isArray(request)) {
            return badRequest(res, 'request must be an object');
          }
          const out = await runAction({ store: deps.store, ctx: deps.ctx,
            actionId, owner: { kind: owner.kind, id: owner.id, generation: owner.generation ?? null },
            op, request, metrics: deps.metrics, tracer: deps.tracer,
            // The caller's W3C context — a span link, never a parent, and
            // absent/invalid headers simply carry no link.
            traceLinks: req.headers?.['traceparent'] ? [String(req.headers['traceparent'])] : [] });
          return sendJson(res, out._status || 200, out);
        }
        case req.method === 'GET' && url.pathname.startsWith('/v1/actions/'): {
          const id = decodeURIComponent(url.pathname.slice('/v1/actions/'.length));
          if (!deps.store) return sendJson(res, 503, { error: 'store_unavailable' });
          const row = await deps.store.get(id);
          if (!row) return sendJson(res, 404, { error: 'unknown action' });
          return sendJson(res, 200, actionResponse(row));
        }
        case req.method === 'PUT' && url.pathname.startsWith('/v1/attachments/'): {
          const key = decodeURIComponent(url.pathname.slice('/v1/attachments/'.length));
          const data = await readBody(req);
          try {
            const r = deps.attachments.put(key, data);
            if (r === 'conflict') {
              return sendJson(res, 409, { error: 'attachment key already holds different bytes' });
            }
            deps.metrics?.inc('bridge_attachment_put_total', { result: r });
            return sendJson(res, 201, { ok: true });
          } catch (err) {
            const bad = /over bound|invalid/.test(String(err?.message || ''));
            return sendJson(res, bad ? (String(err.message).includes('bound') ? 413 : 400) : 500,
              { error: bad ? 'invalid attachment or size' : 'attachment store failed' });
          }
        }
        case req.method === 'GET' && url.pathname.startsWith('/v1/attachments/'): {
          const key = decodeURIComponent(url.pathname.slice('/v1/attachments/'.length));
          const data = deps.attachments.read(key);
          if (data === null) return sendJson(res, 404, { error: 'unknown attachment' });
          res.writeHead(200, { 'content-type': 'application/octet-stream', 'content-length': data.length });
          return res.end(data);
        }
        case route === 'GET /v1/self': {
          const me = deps.self();
          if (!me) return sendJson(res, 503, { error: 'not_connected' });
          return sendJson(res, 200, me);
        }
        case route === 'GET /v1/health': {
          return sendJson(res, 200, deps.health());
        }
        case route === 'POST /v1/reads': {
          const body = JSON.parse((await readBody(req)).toString('utf8') || '{}');
          if (body.op === 'roster') {
            const jid = (body.params || {}).conversation_id;
            if (typeof jid !== 'string' || !isGroupJid(jid)
                || !deps.ctx.sock || !deps.ctx.connected?.()) {
              return sendJson(res, 200, { available: false, members: [] });
            }
            const meta = await deps.ctx.sock.groupMetadata(jid);
            const members = [];
            for (const p of meta?.participants || []) {
              const raw = p.phoneNumber ? `${p.phoneNumber}@s.whatsapp.net` : (p.id || p.lid || '');
              const digits = jidUserDigits(raw);
              if (!digits) continue;
              members.push(isLidJid(raw) ? lidForm(digits) : phoneForm(digits));
            }
            return sendJson(res, 200, { available: true, members });
          }
          return sendJson(res, 404, { error: 'unknown read' });
        }
        default:
          return false;
      }
    } catch (err) {
      if (!res.headersSent) {
        const status = err instanceof SyntaxError ? 400 : (err?.message === 'body too large' ? 413 : 500);
        return sendJson(res, status, { error: status === 400 ? 'invalid json body'
          : status === 413 ? 'body too large' : 'bridge operation failed' });
      }
      res.end();
      return true;
    }
  };
}
