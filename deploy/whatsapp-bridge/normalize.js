/**
 * Bridge event → normalized payload — the JS port of
 * `nora/platform/whatsapp/parser.py:parse_event`, relocated to the bridge
 * boundary per the platform-bridge design (comprehend once at the edge; the
 * app rehydrates, never reparses).
 *
 * Returns `{ kind, upstream, msg }` or `null`. `msg` is the serialized
 * `NormalizedMessage` (the same dict `nora.platform.bridge.rehydrate.
 * message_to_dict` produces and `message_from_dict` consumes); `kind` and
 * `upstream` are the envelope's transition routing fields — distinct
 * message/edit/delete/reaction identities, never one mutable state.
 *
 * The drop list is identical to the app's parser: non-`message`/`reaction`/
 * `delete` types, `from_me` (R-12), broadcast/newsletter JIDs, and turns with
 * neither text nor media.
 */
import {
  conversationIdFor, isBroadcast, isGroupJid, isNewsletter, isPhoneJid,
  isLidJid, lidForm, parseJid, phoneForm, senderForm,
} from './wa_jid.js';
import { labelFor, renderPlaceholder } from './media_labels.js';

function mediaRef(entry) {
  return {
    platform_id: '',
    media_type: String(entry.mimetype || ''),
    filename: null,
    size: (typeof entry.size === 'number' && Number.isInteger(entry.size)) ? entry.size : null,
    voice_note: Boolean(entry.voice_note),
    sticker: false,
    fetch_token: '',
    on_demand: false,
  };
}

function mentionedIds(entries) {
  const out = new Set();
  if (!Array.isArray(entries)) return [];
  for (const m of entries) {
    if (!m || typeof m !== 'object') continue;
    const { phone, lid, jid } = m;
    if (typeof phone === 'string' && phone) out.add(phoneForm(phone));
    if (typeof lid === 'string' && lid) out.add(lidForm(lid));
    if (!phone && !lid && typeof jid === 'string') {
      const parts = parseJid(jid);
      if (parts && isPhoneJid(jid)) out.add(phoneForm(parts.user));
      else if (parts && isLidJid(jid)) out.add(lidForm(parts.user));
    }
  }
  return [...out].sort();
}

function quotedOf(quoted) {
  if (!quoted || typeof quoted !== 'object') return null;
  const ts = quoted.timestamp_ms;
  return {
    quoted_ts: (typeof ts === 'number' && Number.isInteger(ts)) ? ts : 0,
    author_id: senderForm(
      String(quoted.participant_jid || ''),
      typeof quoted.participant_phone === 'string' ? quoted.participant_phone : null,
      typeof quoted.participant_lid === 'string' ? quoted.participant_lid : null,
    ),
    text: String(quoted.text || '').trim(),
  };
}

function targetDict(messageId) {
  return { message_id: messageId, sent_ts: null, mentions_known: true };
}

function touchSenderAndRoom(event) {
  const chatJid = event.chat_jid;
  const isGroup = Boolean(event.is_group) || isGroupJid(chatJid);
  const resolvedPhone = typeof event.sender_phone === 'string' && event.sender_phone
    ? event.sender_phone : null;
  return {
    conversationId: conversationIdFor(chatJid, resolvedPhone),
    conversationType: isGroup ? 'group' : 'dm',
    senderId: senderForm(
      String(event.sender_jid || ''),
      typeof event.sender_phone === 'string' ? event.sender_phone : null,
      typeof event.sender_lid === 'string' ? event.sender_lid : null,
    ),
  };
}

function parseTouch(event) {
  if (event.from_me) return null;
  const chatJid = event.chat_jid;
  const target = String(event.target_id || '');
  if (typeof chatJid !== 'string' || !chatJid || !target) return null;
  if (isBroadcast(chatJid) || isNewsletter(chatJid)) return null;
  const stanza = String(event.id || '');
  const { conversationId, conversationType, senderId } = touchSenderAndRoom(event);
  if (!stanza) {
    // No stable transition identity exists — two id-less touches of one
    // target would collapse to a single upstream key. Retain the event as
    // ambiguous rather than dedupe a guess or invent an id.
    return { ambiguous: {
      reason: `${event.type} stanza has no id`,
      kind: event.type === 'reaction' ? 'reaction' : 'delete',
      conversationId, conversationType, senderId,
      stanzaId: null, targetId: target,
    } };
  }

  let touch;
  let kind;
  if (event.type === 'reaction') {
    const emoji = typeof event.emoji === 'string' ? event.emoji : '';
    touch = {
      kind: 'reaction',
      target: targetDict(target),
      target_author: typeof event.target_author === 'string' ? event.target_author : '',
      emoji,
      removed: Boolean(event.removed) || !emoji,
    };
    kind = 'reaction';
  } else {
    touch = {
      kind: 'delete',
      target: targetDict(target),
      target_author: typeof event.target_author === 'string' ? event.target_author : '',
      emoji: '',
      removed: true,
    };
    kind = 'delete';
  }
  const ts = (typeof event.timestamp_ms === 'number' && Number.isInteger(event.timestamp_ms))
    ? event.timestamp_ms : 0;
  // The transition kind rides in the identity, not just the envelope's kind
  // field: an add→remove pair can arrive reusing the reaction stanza id, and
  // two distinct transitions must never collapse to one event.
  const upstream = kind === 'reaction'
    ? `r:${target}:${stanza}:${senderId}:${touch.removed ? 'remove' : 'add'}`
    : `d:${target}:${stanza}`;
  return {
    kind,
    upstream,
    msg: {
      platform: 'whatsapp',
      conversation_id: conversationId,
      conversation_type: conversationType,
      sender_id: senderId,
      sender_name: event.push_name || null,
      timestamp: ts,
      body: '',
      raw: event,
      mentions: [],
      quote: null,
      media: [],
      message_id: stanza,
      media_blobs: [],
      external_files: [],
      voice: false,
      thread: '',
      edit_of: null,
      touch,
    },
  };
}

/**
 * One bridge event → `{ kind, upstream, msg }` or `null`. Mirrors
 * `parse_event`: never throws — a malformed event is logged and skipped so a
 * bad turn never kills the capture loop behind it.
 */
export function parseEvent(event, { log = () => {} } = {}) {
  try {
    if (!event || typeof event !== 'object') return null;
    if (event.type === 'reaction' || event.type === 'delete') {
      return parseTouch(event);
    }
    if (event.type !== 'message') return null;      // heartbeat/dropped: listener-level
    if (event.from_me) return null;                 // R-12's first pass
    const chatJid = event.chat_jid;
    if (typeof chatJid !== 'string' || !chatJid) return null;
    if (isBroadcast(chatJid) || isNewsletter(chatJid)) return null;

    const refs = (Array.isArray(event.media) ? event.media : [])
      .filter((m) => m && typeof m === 'object')
      .map(mediaRef);
    const parts = [];
    const text = String(event.text || '').trim();
    if (text) parts.push(text);
    for (const ref of refs) parts.push(renderPlaceholder(labelFor(ref)));
    if (!parts.length) return null;                 // neither text nor media

    const isGroup = Boolean(event.is_group) || isGroupJid(chatJid);
    let resolvedPhone = event.sender_phone;
    if (!(typeof resolvedPhone === 'string' && resolvedPhone)) {
      const parts = parseJid(chatJid);
      resolvedPhone = parts && isPhoneJid(chatJid) ? parts.user : null;
    }
    const senderId = senderForm(
      String(event.sender_jid || ''),
      typeof event.sender_phone === 'string' ? event.sender_phone : null,
      typeof event.sender_lid === 'string' ? event.sender_lid : null,
    );
    const ts = (typeof event.timestamp_ms === 'number' && Number.isInteger(event.timestamp_ms))
      ? event.timestamp_ms : 0;
    const editOf = event.edit_of ? String(event.edit_of) : null;
    const stanza = String(event.id || '');
    const kind = editOf ? 'edit' : 'message';
    if (!stanza) {
      return { ambiguous: {
        reason: `${kind} stanza has no id`,
        kind, conversationId: conversationIdFor(chatJid, resolvedPhone),
        conversationType: isGroup ? 'group' : 'dm',
        senderId, stanzaId: null, targetId: editOf,
      } };
    }
    const upstream = editOf ? `e:${editOf}:${stanza}` : `m:${stanza}:${senderId}`;

    return {
      kind,
      upstream,
      msg: {
        platform: 'whatsapp',
        conversation_id: conversationIdFor(chatJid, resolvedPhone),
        conversation_type: isGroup ? 'group' : 'dm',
        sender_id: senderId,
        sender_name: event.push_name || null,
        timestamp: ts,
        body: parts.join(' '),
        raw: event,
        mentions: mentionedIds(event.mentioned),
        quote: quotedOf(event.quoted),
        media: refs,
        message_id: stanza,
        media_blobs: [],
        external_files: [],
        voice: false,
        thread: '',
        edit_of: editOf ? targetDict(editOf) : null,
        touch: null,
      },
    };
  } catch (err) {
    log(`unparseable whatsapp event, skipping: ${err?.message || err}`);
    return null;
  }
}
