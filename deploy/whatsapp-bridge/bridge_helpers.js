/**
 * Pure helpers for the Nora WhatsApp bridge (deploy/whatsapp-bridge/bridge.js).
 *
 * Everything in this module is free of Baileys sockets, HTTP, and wall-clock
 * side effects beyond injected ones, so `bridge.test.mjs` can exercise the
 * parsing/resolution/buffering rules without pairing. Shapes are pinned by
 * design/whatsapp-adoption/03-transport-contract.md — drift there is a
 * contract failure, not a judgment call.
 *
 * Adapted from Hermes' scripts/whatsapp-bridge/ (MIT). Not copied: the
 * long-poll queue (replaced by SSE + drop-and-count buffer), media custody,
 * allowlist/mode gating, polls, and the Hermes env coupling.
 */

import { createDecipheriv, hkdfSync, timingSafeEqual } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

// -- JID parsing and LID<->phone resolution --------------------------------

/**
 * Strip the WhatsApp multi-device device suffix from the user part:
 * "85290000100:10@s.whatsapp.net" -> "85255080100@s.whatsapp.net".
 */
export function normalizeJid(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const at = raw.indexOf('@');
  if (at === -1) return raw;
  const user = raw.slice(0, at).split(':', 1)[0];
  return `${user}@${raw.slice(at + 1)}`;
}

export function jidDomain(jid) {
  const raw = String(jid || '');
  const at = raw.indexOf('@');
  return at === -1 ? '' : raw.slice(at + 1);
}

/** Digit string of the user part (device suffix stripped), or '' if none. */
export function jidUserDigits(jid) {
  const raw = String(jid || '');
  const at = raw.indexOf('@');
  const user = (at === -1 ? raw : raw.slice(0, at)).split(':', 1)[0];
  const digits = user.replace(/\D/g, '');
  return digits;
}

export function isGroupJid(jid) {
  return jidDomain(jid) === 'g.us';
}

export function isLidJid(jid) {
  return jidDomain(jid) === 'lid';
}

/** Outbound send targets must be a person, a group, or a LID — nothing else. */
export function isValidTargetJid(jid) {
  return ['s.whatsapp.net', 'g.us', 'lid'].includes(jidDomain(jid));
}

/**
 * LID -> phone reverse map from Baileys' multi-file auth state. Baileys
 * persists one lid-mapping-<phone>.json file per resolved identity; the file
 * content is a JSON-encoded LID digit string (or array in some versions).
 */
export function buildLidMap(sessionDir) {
  const map = {};
  let entries = [];
  try {
    entries = readdirSync(sessionDir);
  } catch {
    return map;
  }
  for (const file of entries) {
    const m = file.match(/^lid-mapping-(\d+)\.json$/);
    if (!m) continue;
    const phone = m[1];
    try {
      const parsed = JSON.parse(readFileSync(path.join(sessionDir, file), 'utf8'));
      const lids = Array.isArray(parsed) ? parsed : [parsed];
      for (const lid of lids) {
        const digits = String(lid || '').replace(/\D/g, '');
        if (digits) map[digits] = phone;
      }
    } catch {
      // A half-written mapping file must not break every other entry.
    }
  }
  return map;
}

/**
 * Resolve a JID to the contract's digit forms: {phone, lid}, each null when
 * unknown. Phone JIDs yield phone directly; LID JIDs yield lid plus the
 * phone when the reverse map knows it. Group/broadcast JIDs yield both null.
 */
export function resolveIdentity(jid, lidToPhone = {}, phoneToLid = {}) {
  const domain = jidDomain(jid);
  if (domain === 's.whatsapp.net') {
    const phone = jidUserDigits(jid) || null;
    return { phone, lid: phone ? phoneToLid[phone] || null : null };
  }
  if (domain === 'lid') {
    const lid = jidUserDigits(jid) || null;
    return { phone: lid ? lidToPhone[lid] || null : null, lid };
  }
  return { phone: null, lid: null };
}

/**
 * phone -> lid, the inverse of buildLidMap's lid -> phone. Both directions
 * ride every message event: the Python adapter's MentionPolicy contract is a
 * membership test against BOTH forms (design/whatsapp-adoption/03), so a
 * sender or mention known only as a phone JID must still carry its lid when
 * the session map knows one.
 */
export function invertLidMap(lidToPhone) {
  const out = {};
  for (const [lid, phone] of Object.entries(lidToPhone || {})) {
    if (!(phone in out)) out[phone] = lid;
  }
  return out;
}

// -- Numeric coercion (protobufjs may surface Long objects) ----------------

export function toNumber(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'object' && typeof value.toNumber === 'function') {
    const n = value.toNumber();
    return Number.isFinite(n) ? n : null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Baileys messageTimestamp is seconds; the contract speaks milliseconds. */
export function timestampMs(value) {
  const n = toNumber(value);
  return n === null ? null : n * 1000;
}

// -- Recent-message cache (quoted timestamps + /react key lookup) ----------

export function createRecentMessageCache(limit = 1000) {
  const byId = new Map();

  function remember(msg) {
    const id = msg?.key?.id;
    if (!id) return;
    byId.delete(id);
    byId.set(id, {
      id,
      chat_jid: msg.key.remoteJid || null,
      sender_jid: msg.key.participant || msg.key.remoteJid || null,
      from_me: Boolean(msg.key.fromMe),
      timestamp_ms: timestampMs(msg.messageTimestamp),
      // The per-message secret an EDIT of this message is encrypted with
      // (`secretEncryptedMessage`, see `decryptSecretEdit`), and the sender's
      // other JID form, which the key derivation may have used instead.
      message_secret: messageSecretOf(msg),
      sender_alt: msg.key.participantAlt || msg.key.remoteJidAlt || null,
      key: {
        id,
        remoteJid: msg.key.remoteJid,
        fromMe: Boolean(msg.key.fromMe),
        ...(msg.key.participant ? { participant: msg.key.participant } : {}),
      },
    });
    while (byId.size > limit) {
      byId.delete(byId.keys().next().value);
    }
  }

  function get(id) {
    if (!id || !byId.has(id)) return null;
    const entry = byId.get(id);
    byId.delete(id);
    byId.set(id, entry);
    return entry;
  }

  return { remember, get, get size() { return byId.size; } };
}

// -- Secret-encrypted edits (`secretEncryptedMessage`, MESSAGE_EDIT) -------

/**
 * WhatsApp phones now send an edit ENCRYPTED with the original message's
 * `messageContextInfo.messageSecret`, not as a plain `protocolMessage`
 * (measured live 2026-09-24: `outer=[messageContextInfo,
 * secretEncryptedMessage]`). Baileys up to rc14 does not decrypt it, so the
 * bridge does, porting whatsmeow's `decryptMsgSecret` (msgsecret.go):
 *
 *   key = HKDF-SHA256(ikm = messageSecret, salt = none,
 *                     info = origId + origSender + editor + "Message Edit", 32)
 *   plaintext = AES-256-GCM(key, encIv, encPayload), no additional data
 *
 * with both JIDs in their non-device form. WhatsApp is mid-migration to LIDs,
 * so which form (phone or LID) each side used is not knowable in advance;
 * every candidate pair is tried and GCM's tag rejects a wrong key, so trying
 * is safe. The secret comes from the recent-message cache: an edit of a
 * message older than the cache, or from before a bridge restart, cannot be
 * decrypted and is reported as such. `bridge.test.mjs` pins the derivation
 * against a vector whatsmeow itself produced.
 */
export const SECRET_ENC_MESSAGE_EDIT = 2;
const MESSAGE_EDIT_USE_CASE = 'Message Edit';

export function messageSecretOf(msg) {
  const secret = msg?.message?.messageContextInfo?.messageSecret
    || getMessageContent(msg)?.messageContextInfo?.messageSecret;
  if (!secret) return null;
  if (typeof secret === 'string') return Buffer.from(secret, 'base64');
  return Buffer.from(secret);
}

export function deriveEditKey(secret, origId, origSender, editor) {
  const info = Buffer.from(`${origId}${origSender}${editor}${MESSAGE_EDIT_USE_CASE}`, 'utf8');
  // `hkdfSync` THROWS on info over 1024 bytes, and the id is sender-chosen:
  // an over-long one must be «cannot decrypt», never an exception out of the
  // upsert loop that skips the rest of the batch.
  if (info.length > 1024) return null;
  return Buffer.from(hkdfSync('sha256', secret, Buffer.alloc(0), info, 32));
}

function gcmOpen(key, iv, payload) {
  const data = Buffer.from(payload);
  if (data.length < 17) return null;
  try {
    const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(iv));
    decipher.setAuthTag(data.subarray(data.length - 16));
    return Buffer.concat([decipher.update(data.subarray(0, data.length - 16)), decipher.final()]);
  } catch {
    return null;                        // wrong key (tag mismatch) or malformed
  }
}

function jidForms(jid, lidToPhone, phoneToLid) {
  const out = new Set();
  const base = normalizeJid(jid);
  if (!base) return out;
  out.add(base);
  const id = resolveIdentity(base, lidToPhone, phoneToLid);
  if (id.phone) out.add(`${id.phone}@s.whatsapp.net`);
  if (id.lid) out.add(`${id.lid}@lid`);
  return out;
}

/**
 * A `secretEncryptedMessage` MESSAGE_EDIT → `{ msg }`, a Baileys-shaped message
 * carrying the plain `protocolMessage` edit `extractMessageEvent` already
 * understands; or `{ reason }` saying why it could not be. `decode` is
 * Baileys' `proto.Message.decode`, injected so this module stays pure.
 */
export function decryptSecretEdit(msg, { recentCache, decode, lidToPhone = {}, phoneToLid = {} } = {}) {
  const sem = getMessageContent(msg)?.secretEncryptedMessage;
  if (!sem) return { reason: 'not_secret_encrypted' };
  if (Number(sem.secretEncType) !== SECRET_ENC_MESSAGE_EDIT) return { reason: 'not_a_message_edit' };
  const target = sem.targetMessageKey || {};
  const cached = recentCache ? recentCache.get(target.id) : null;
  if (!target.id || !cached || !cached.message_secret) return { reason: 'secret_unknown' };
  if (!sem.encIv || !sem.encPayload) return { reason: 'malformed' };

  const editorForms = new Set([
    ...jidForms(msg.key.participant || msg.key.remoteJid, lidToPhone, phoneToLid),
    ...jidForms(msg.key.participantAlt || msg.key.remoteJidAlt, lidToPhone, phoneToLid),
  ]);
  // The EDITOR first: only a sender edits their own message, and whatsmeow's
  // `getOrigSenderFromKey` takes the editor as the original's sender for a
  // `fromMe` key — its first choice. Then every form the cache and the target
  // key know, for the phone/LID migration.
  const origForms = new Set([
    ...editorForms,
    ...jidForms(target.participant || target.remoteJid, lidToPhone, phoneToLid),
    ...jidForms(cached.sender_jid, lidToPhone, phoneToLid),
    ...jidForms(cached.sender_alt, lidToPhone, phoneToLid),
  ]);
  for (const orig of origForms) {
    for (const editor of editorForms) {
      const key = deriveEditKey(cached.message_secret, target.id, orig, editor);
      const plain = key && gcmOpen(key, sem.encIv, sem.encPayload);
      if (!plain) continue;
      let decoded;
      try {
        decoded = decode(plain);
      } catch {
        return { reason: 'undecodable' };
      }
      return synthesizeEdit(msg, target, decoded);
    }
  }
  return { reason: 'undecryptable' };
}

/**
 * The decrypted plaintext in whichever shape it arrives. mautrix-whatsapp
 * reads it as a WHOLE message that is itself an edit — a `protocolMessage`
 * MESSAGE_EDIT carrying `editedMessage` and `timestampMs`, possibly inside the
 * `editedMessage` shell `getMessageContent` unwraps — so that shape is
 * preferred, with its own millisecond clock; a bare content message (the new
 * text itself) is the fallback. Reading the wrapped shape as bare content
 * produced an event with no text, which the adapter drops: every edit lost
 * while the log said it had worked (review, 2026-09-24). `shape` is the
 * decoded message's top-level KEYS, for the log, never a value.
 */
function synthesizeEdit(msg, target, decoded) {
  const inner = getMessageContent({ message: decoded })?.protocolMessage;
  const isEdit = inner && (inner.type === 14 || inner.type === 'MESSAGE_EDIT') && inner.editedMessage;
  const editedMessage = isEdit ? inner.editedMessage : decoded;
  const innerMs = isEdit ? toNumber(inner.timestampMs) : null;
  const seconds = toNumber(msg.messageTimestamp);
  const timestampMs = innerMs || (seconds !== null ? seconds * 1000 : null);
  const content = getMessageContent({ message: editedMessage });
  const hasBody = Boolean(ownText(content, mediaDescriptors(content)) || mediaDescriptors(content).length);
  const shape = Object.keys(decoded || {}).join(',');
  if (!hasBody) return { reason: 'empty_edit', shape };
  return {
    shape,
    msg: {
      ...msg,
      message: {
        protocolMessage: {
          type: 14,
          key: { id: target.id, remoteJid: target.remoteJid, fromMe: Boolean(target.fromMe),
            ...(target.participant ? { participant: target.participant } : {}) },
          editedMessage,
          ...(timestampMs !== null ? { timestampMs } : {}),
        },
      },
    },
  };
}

// -- Bounded event buffer with drop-and-count (contract §dropped) ----------

/**
 * While no SSE client is connected, message events sit here (cap 500).
 * Overflow drops the OLDEST and increments the counter — never Hermes'
 * silent messageQueue.shift(). The counter is permanent; /health exposes it.
 */
export function createEventBuffer(capacity = 500, { onDrop } = {}) {
  const events = [];
  let dropped = 0;

  function push(event) {
    events.push(event);
    while (events.length > capacity) {
      events.shift();
      dropped += 1;
      if (onDrop) onDrop(dropped);
    }
    return dropped;
  }

  function snapshot() {
    return events.slice();
  }

  function clear() {
    events.length = 0;
  }

  return {
    push,
    snapshot,
    clear,
    get size() { return events.length; },
    get dropped() { return dropped; },
  };
}

// -- Reconnect scheduler: bounded exponential backoff + jitter --------------

/**
 * Every (re)connect goes through this scheduler. Backoff doubles per failed
 * cycle up to maxDelayMs with +/-50% jitter; reset() on a successful open.
 * A pending timer suppresses duplicate schedules (connection 'close' can
 * fire more than once per cycle). Rejections from startFn are caught and
 * retried — a bare setTimeout(startFn) would wedge the bridge (Hermes
 * learned this the hard way: 27h silent disconnect).
 */
export function createReconnectScheduler(startFn, {
  baseDelayMs = 1000,
  maxDelayMs = 60000,
  log = () => {},
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
  rand = Math.random,
} = {}) {
  let attempts = 0;
  let pending = null;

  function nextDelay() {
    const ceiling = Math.min(maxDelayMs, baseDelayMs * 2 ** attempts);
    // jitter in [0.5, 1.0) * ceiling: never zero, never past the cap
    return Math.round(ceiling * (0.5 + rand() / 2));
  }

  function fire() {
    pending = null;
    Promise.resolve()
      .then(startFn)
      .catch((err) => {
        attempts += 1;
        const delay = nextDelay();
        log(`[bridge] reconnect failed (${err?.message || err}); retry ${attempts} in ${delay}ms`);
        schedule(delay);
      });
  }

  function schedule(delayMs) {
    if (pending !== null) return;
    pending = setTimeoutFn(fire, delayMs);
  }

  function scheduleReconnect() {
    if (pending !== null) return;
    const delay = nextDelay();
    log(`[bridge] scheduling reconnect attempt ${attempts + 1} in ${delay}ms`);
    schedule(delay);
  }

  function reset() {
    attempts = 0;
  }

  function cancel() {
    if (pending !== null) {
      clearTimeoutFn(pending);
      pending = null;
    }
  }

  return { scheduleReconnect, reset, cancel, get attempts() { return attempts; } };
}

// -- Inbound message extraction (contract §message event) -------------------

/**
 * Unwrap the "future-proof" shells — ephemeral, view-once, document-with-
 * caption, and `editedMessage` — as many levels deep as they nest.
 *
 * `editedMessage` is how a PHONE sends an edit: `{editedMessage: {message:
 * {protocolMessage: {type: MESSAGE_EDIT, …}}}}`. Baileys' own
 * `normalizeMessageContent` unwraps it in a bounded loop (rc13,
 * `Utils/messages.js`); this function unwrapped one fixed level and not that
 * shell at all, so a phone's edit reached `extractMessageEvent` with no
 * `protocolMessage`, no text and no media, and was dropped as an empty turn —
 * silently, before Nora could see it (design/message-edits audit, 2026-09-24).
 * Bounded at 5 like Baileys', so a malformed self-nesting message cannot loop.
 */
export function getMessageContent(msg) {
  let content = msg?.message || {};
  for (let i = 0; i < 5; i++) {
    const inner = (
      content.ephemeralMessage ||
      content.viewOnceMessage ||
      content.viewOnceMessageV2 ||
      content.viewOnceMessageV2Extension ||
      content.documentWithCaptionMessage ||
      content.editedMessage
    )?.message;
    if (!inner) break;
    content = inner;
  }
  return content;
}

/** contextInfo can hang off any content value; scan rather than guess which. */
export function getContextInfo(messageContent) {
  if (!messageContent || typeof messageContent !== 'object') return {};
  for (const value of Object.values(messageContent)) {
    if (value && typeof value === 'object' && value.contextInfo) {
      return value.contextInfo;
    }
  }
  return {};
}

function textFromQuotedMessage(quotedMessage) {
  if (!quotedMessage || typeof quotedMessage !== 'object') return null;
  const inner = getMessageContent({ message: quotedMessage });
  if (inner.conversation) return inner.conversation;
  if (inner.extendedTextMessage?.text) return inner.extendedTextMessage.text;
  if (inner.imageMessage?.caption) return inner.imageMessage.caption;
  if (inner.videoMessage?.caption) return inner.videoMessage.caption;
  if (inner.documentMessage?.caption) return inner.documentMessage.caption;
  if (inner.audioMessage) return null;
  return null;
}

/** Media descriptors only — kind/mimetype/duration/size/caption. NO bytes. */
function mediaDescriptors(messageContent) {
  const out = [];
  const img = messageContent.imageMessage;
  if (img) {
    out.push({
      kind: 'image',
      mimetype: img.mimetype || null,
      voice_note: false,
      seconds: null,
      size: toNumber(img.fileLength),
      caption: img.caption || null,
      filename: null,
    });
  }
  const vid = messageContent.videoMessage;
  if (vid) {
    out.push({
      kind: 'video',
      mimetype: vid.mimetype || null,
      voice_note: false,
      seconds: toNumber(vid.seconds),
      size: toNumber(vid.fileLength),
      caption: vid.caption || null,
      filename: null,
    });
  }
  const aud = messageContent.audioMessage || messageContent.pttMessage;
  if (aud) {
    out.push({
      kind: 'audio',
      mimetype: aud.mimetype || null,
      voice_note: Boolean(aud.ptt || messageContent.pttMessage),
      seconds: toNumber(aud.seconds),
      size: toNumber(aud.fileLength),
      caption: null,
      filename: null,
    });
  }
  const stk = messageContent.stickerMessage;
  if (stk) {
    out.push({
      kind: 'sticker',
      mimetype: stk.mimetype || null,
      voice_note: false,
      seconds: null,
      size: toNumber(stk.fileLength),
      caption: null,
      filename: null,
    });
  }
  const doc = messageContent.documentMessage;
  if (doc) {
    out.push({
      kind: 'document',
      mimetype: doc.mimetype || null,
      voice_note: false,
      seconds: null,
      size: toNumber(doc.fileLength),
      caption: doc.caption || null,
      filename: doc.fileName || null,
    });
  }
  return out;
}

function ownText(messageContent, media) {
  if (messageContent.conversation) return messageContent.conversation;
  if (messageContent.extendedTextMessage?.text) return messageContent.extendedTextMessage.text;
  for (const entry of media) {
    if (entry.caption) return entry.caption;
  }
  return null;
}

/**
 * Build the contract `message` event from one Baileys proto message.
 * Returns null for messages with no sender-visible content at all (pure
 * protocol noise that is not a MESSAGE_EDIT). Caller decides buffering.
 *
 * protocolMessage MESSAGE_EDIT surfaces as a normal event with `edit_of`
 * set to the target stanza id; text/media then describe the NEW content.
 */
export function extractMessageEvent(msg, { lidToPhone = {}, phoneToLid = {}, recentCache = null } = {}) {
  if (!msg?.key?.remoteJid || !msg.message) return null;

  const chatJid = normalizeJid(msg.key.remoteJid) || msg.key.remoteJid;
  const group = isGroupJid(chatJid);
  const senderJidRaw = group && msg.key.participant ? msg.key.participant : msg.key.remoteJid;
  const senderJid = normalizeJid(senderJidRaw) || senderJidRaw;
  const sender = resolveIdentity(senderJid, lidToPhone, phoneToLid);

  let content = getMessageContent(msg);
  let editOf = null;

  // A REACTION or a REVOKE (delete for everyone) is not a message: it names
  // the message it touches and says nothing itself. Emitted as its own event
  // type so Nora records it BESIDE that message, never as a transcript line
  // (design/cognition-loop/20-observe.md §1). Both used to fall through to
  // the empty-turn drop below and were lost.
  const who = {
    id: msg.key.id || null,
    chat_jid: chatJid,
    is_group: group,
    from_me: Boolean(msg.key.fromMe),
    sender_jid: senderJid,
    sender_phone: sender.phone,
    sender_lid: sender.lid,
    push_name: msg.pushName || null,
  };
  const reaction = content.reactionMessage;
  if (reaction) {
    const target = reaction.key?.id || null;
    if (!target) return null;
    const emoji = typeof reaction.text === 'string' ? reaction.text : '';
    return {
      type: 'reaction',
      ...who,
      target_id: target,
      // An EMPTY text is WhatsApp's «reaction removed».
      emoji,
      removed: emoji === '',
      timestamp_ms: toNumber(reaction.senderTimestampMs) || timestampMs(msg.messageTimestamp),
    };
  }

  const proto = content.protocolMessage;
  // Baileys proto constants: REVOKE = 0 (a string in JSON land).
  if (proto && (proto.type === 'REVOKE' || proto.type === 0)) {
    const target = proto.key?.id || null;
    if (!target) return null;
    return {
      type: 'delete',
      ...who,
      target_id: target,
      timestamp_ms: timestampMs(msg.messageTimestamp),
    };
  }
  if (proto) {
    // Baileys proto constants: MESSAGE_EDIT = 14 (string in JSON land).
    if (proto.type !== 'MESSAGE_EDIT' && proto.type !== 14) return null;
    editOf = proto.key?.id || null;
    if (!editOf || !proto.editedMessage) return null;
    content = getMessageContent({ message: proto.editedMessage });
  }

  const contextInfo = getContextInfo(content);
  const media = mediaDescriptors(content);
  const text = ownText(content, media);

  const mentioned = (contextInfo?.mentionedJid || [])
    .map((raw) => {
      const jid = normalizeJid(raw);
      if (!jid) return null;
      const id = resolveIdentity(jid, lidToPhone, phoneToLid);
      return { jid, phone: id.phone, lid: id.lid };
    })
    .filter(Boolean);

  let quoted = null;
  if (contextInfo?.stanzaId || contextInfo?.quotedMessage) {
    const participantJid = normalizeJid(contextInfo?.participant) || null;
    const participant = participantJid ? resolveIdentity(participantJid, lidToPhone, phoneToLid) : { phone: null, lid: null };
    const cached = recentCache ? recentCache.get(contextInfo?.stanzaId) : null;
    quoted = {
      id: contextInfo?.stanzaId || null,
      participant_jid: participantJid,
      participant_phone: participant.phone,
      participant_lid: participant.lid,
      // null when the parent predates the recent-message cache: the reply
      // relation survives, the parent pointer does not.
      timestamp_ms: cached ? cached.timestamp_ms : null,
      text: textFromQuotedMessage(contextInfo?.quotedMessage),
    };
  }

  if (!text && media.length === 0 && !editOf) return null;

  return {
    type: 'message',
    id: msg.key.id || null,
    chat_jid: chatJid,
    is_group: group,
    from_me: Boolean(msg.key.fromMe),
    sender_jid: senderJid,
    sender_phone: sender.phone,
    sender_lid: sender.lid,
    push_name: msg.pushName || null,
    text,
    // An edit's own clock when it carries one: `protocolMessage.timestampMs`
    // is milliseconds, where `messageTimestamp` is whole seconds — two edits
    // in one second would otherwise tie, and the later could be discarded.
    timestamp_ms: (editOf && toNumber(proto?.timestampMs)) || timestampMs(msg.messageTimestamp),
    mentioned,
    quoted,
    media,
    edit_of: editOf,
  };
}

// -- Outbound payload shaping ------------------------------------------------

const MIME_BY_EXT = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
  webp: 'image/webp', gif: 'image/gif',
  mp4: 'video/mp4', mov: 'video/quicktime', '3gp': 'video/3gpp',
  ogg: 'audio/ogg; codecs=opus', opus: 'audio/ogg; codecs=opus',
  mp3: 'audio/mpeg', m4a: 'audio/mp4', wav: 'audio/wav',
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

export function mimeForFilename(filename) {
  const ext = String(filename || '').toLowerCase().split('.').pop();
  return MIME_BY_EXT[ext] || null;
}

/**
 * Build a Baileys sendMessage payload for /send-media. Kind comes from the
 * explicit media_type field; mimetype from the filename extension when the
 * caller did not pass one.
 */
export function buildMediaPayload({ mediaType, buffer, filename, caption, mimetype }) {
  const mime = mimetype || mimeForFilename(filename);
  switch (mediaType) {
    case 'image':
      return { image: buffer, ...(caption ? { caption } : {}), ...(mime ? { mimetype: mime } : {}) };
    case 'video':
      return { video: buffer, ...(caption ? { caption } : {}), ...(mime ? { mimetype: mime } : {}) };
    case 'audio':
      return { audio: buffer, mimetype: mime || 'audio/ogg; codecs=opus' };
    case 'sticker':
      return { sticker: buffer, mimetype: mime || 'image/webp' };
    case 'document':
      return {
        document: buffer,
        fileName: filename || 'file',
        ...(caption ? { caption } : {}),
        mimetype: mime || 'application/octet-stream',
      };
    default:
      return null;
  }
}

// -- Logging -----------------------------------------------------------------

/**
 * Redact a JID to its last 4 user digits + domain: enough to correlate
 * log lines, never enough to reconstruct a number. Message text is never
 * logged anywhere in the bridge.
 */
export function redactJid(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const domain = jidDomain(raw);
  const digits = jidUserDigits(raw);
  const suffix = digits ? digits.slice(-4) : raw.slice(0, 4);
  return `…${suffix}${domain ? `@${domain}` : ''}`;
}

/** Baileys wants a pino-shaped logger; give it a silent one (we log ourselves). */
export function createSilentLogger() {
  const noop = () => {};
  const logger = {
    level: 'silent',
    trace: noop, debug: noop, info: noop, warn: noop, error: noop, fatal: noop,
    child: () => logger,
  };
  return logger;
}

export function ensureDir(dir) {
  mkdirSync(dir, { recursive: true });
}

// -- Bridge authentication ---------------------------------------------------

/**
 * True only for the compose healthcheck (`GET /health`, no account data) or a
 * request carrying the exact shared bearer. An empty configured token answers
 * false for every non-health route: the bridge refuses to start without one,
 * and this stays fail-closed if that check is ever bypassed.
 */
export function authorizedBridgeRequest(method, pathname, authorization, token) {
  if (method === 'GET' && pathname === '/health') return true;
  if (!token || typeof authorization !== 'string') return false;
  // Compare BYTE lengths, not JavaScript string lengths. A non-ASCII header can
  // have the expected string length and a different UTF-8 byte length, and
  // `timingSafeEqual` throws ERR_CRYPTO_TIMING_SAFE_EQUAL_LENGTH on that.
  const got = Buffer.from(authorization, 'utf8');
  const expected = Buffer.from(`Bearer ${token}`, 'utf8');
  if (got.length !== expected.length) return false;
  return timingSafeEqual(got, expected);
}
