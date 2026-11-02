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
import { contentHash, observationProblem } from './observations.js';

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
  // The transition kind AND the source's own clock ride in the identity:
  // an add→remove→add sequence can reuse the stanza id, and the upstream
  // reaction timestamp distinguishes a re-add from a replay. When the
  // source clock is absent the `na` form retains the ambiguity honestly —
  // no fabricated discriminator, no global sequence invented.
  const clock = ts ? String(ts) : 'na';
  const upstream = kind === 'reaction'
    ? `r:${target}:${stanza}:${senderId}:${touch.removed ? 'remove' : 'add'}:${clock}`
    : `d:${target}:${stanza}`;
  // A clockless (`na`) reaction re-transition reuses the FIRST add's
  // upstream key byte-for-byte — the ledger, not source-key dedupe, must
  // decide whether it is a replay or a genuine change. The upstream
  // reaction clock is the seq: a re-add at a later source time is forward
  // progress, at an earlier one a stale replay. One reaction slot exists
  // per (sender, target) — a removal names no emoji, so the emoji rides
  // the state, never the key.
  const transitions = kind === 'reaction' ? [{
    key: `${conversationId}|reaction|${target}|${senderId}`,
    state: touch.removed ? 'remove' : `add:${touch.emoji}`,
    upstream_seq: ts || 0,
  }] : [];
  return {
    kind,
    upstream,
    transitions,
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
 * The transition-ledger declarations an observation commits: the durable
 * record of "this upstream event advanced object X to state S". Content-
 * derived upstreams (the stanza-less `gu`/`gpu`/`gus`/`gjr`/`puc` callback
 * surfaces) reproduce byte-identically on a genuine A→B→A re-transition,
 * so the source key alone cannot tell a replay from a change — the ledger
 * holds the committed states and the capture resolves emit/replay/
 * ambiguous against it, exactly as the Instagram bridge's capture does.
 *
 * `state` is the VALUE the observation claims (field-set hash, member-set
 * hash, subject op+role+state), never the actor — who acted is not part of
 * what changed. The clock is the source's own when it exists — a stanza
 * timestamp (`occurred_ms` under `upstream` quality) is a real forward/
 * backward watermark; the stanza-less callbacks carry none, so seq 0 is
 * honest: identical repeat → replay; a state the key already committed →
 * ambiguous; a new state → genuine transition.
 */
export function observationTransitions(obs, conversationId) {
  if (!obs || typeof obs !== 'object') return [];
  const out = [];
  const payload = obs.payload && typeof obs.payload === 'object' ? obs.payload : {};
  const seq = (typeof obs.source_position === 'number' && Number.isInteger(obs.source_position))
    ? obs.source_position
    : (obs.time_quality === 'upstream' && typeof obs.occurred_ms === 'number'
      ? obs.occurred_ms : 0);
  switch (obs.kind) {
    case 'room': {
      const fields = payload.fields;
      if (fields && typeof fields === 'object' && Object.keys(fields).length) {
        out.push({
          key: `${conversationId}|room|fields`,
          state: `v:${contentHash(fields)}`,
          upstream_seq: seq,
        });
      }
      break;
    }
    case 'membership': {
      const covered = String(payload.covered || '');
      if (covered === 'members' || covered === 'admins') {
        // Covered-set snapshots: the reported set IS the state, including
        // the empty set — a set returning to an earlier membership without
        // a source revision is unresolvable, not a redelivery.
        const members = Array.isArray(payload.members) ? payload.members : [];
        out.push({
          key: `${conversationId}|${covered}`,
          state: `v:${contentHash(members)}`,
          upstream_seq: seq,
        });
        // The snapshot also commits each reported subject's own presence on
        // its member key: a remove→rejoin→remove where the rejoin arrives
        // only inside a covered-set snapshot then reads as remove→member→
        // remove on that key — the second departure is honestly ambiguous
        // rather than an identical repeat of the last committed state.
        for (const mb of members) {
          const subject = mb && typeof mb === 'object' ? mb.subject : null;
          const state = mb && typeof mb === 'object' ? mb.state : null;
          const role = mb && typeof mb === 'object' ? mb.role : null;
          if (typeof subject === 'string' && subject) {
            out.push({
              key: `${conversationId}|member|${subject}`,
              state: ['snapshot', role || '', state || 'member'].join('|'),
              upstream_seq: seq,
            });
          }
        }
      } else if (typeof obs.subject_ref === 'string' && obs.subject_ref) {
        // Per-subject deltas (add/remove/leave/promote/demote/join-request):
        // key on the subject so a clockless remove→add→remove is ambiguous
        // rather than a silent dedupe of the second removal.
        out.push({
          key: `${conversationId}|member|${obs.subject_ref}`,
          state: [obs.operation, payload.role || '', payload.state || ''].join('|'),
          upstream_seq: seq,
        });
      }
      break;
    }
    case 'pin': {
      const targetId = obs.target?.message_id;
      if (typeof targetId === 'string' && targetId
        && (obs.operation === 'add' || obs.operation === 'remove')) {
        out.push({
          key: `${conversationId}|pin|${targetId}`,
          state: obs.operation,
          upstream_seq: seq,
        });
      }
      break;
    }
    default:
      break;
  }
  return out;
}

/**
 * One bridge event → `{ kind, upstream, msg }` or `null`. Mirrors
 * `parse_event`: never throws — a malformed event is logged and skipped so a
 * bad turn never kills the capture loop behind it.
 */
export function parseEvent(event, { log = () => {} } = {}) {
  try {
    if (!event || typeof event !== 'object') return null;
    if (event.type === 'observation') {
      // A typed observation event (observations.js): validate the wire dict
      // here so a malformed observation fails closed at the boundary and
      // never enters the journal as a best-effort record.
      const obs = event.observation;
      const problem = observationProblem(obs);
      const upstream = String(event.upstream || '');
      if (problem || !upstream) {
        log(`unparseable whatsapp observation (${problem || 'no upstream identity'}), skipping`);
        return null;
      }
      return {
        kind: obs.kind,
        upstream,
        transitions: observationTransitions(obs, String(event.conversation_id || '')),
        msg: null,
        observation: obs,
        route: {
          conversation_id: String(event.conversation_id || ''),
          conversation_type: event.conversation_type === 'dm' ? 'dm' : 'group',
          sender_id: String(event.sender_id || ''),
          sender_name: event.sender_name || null,
          sent_ms: (typeof event.sent_ms === 'number' && Number.isInteger(event.sent_ms))
            ? event.sent_ms : null,
          time_quality: event.time_quality === 'upstream' ? 'upstream' : 'bridge_local',
        },
      };
    }
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

    // A source stanza that is BOTH an ordinary turn and an object event
    // carries the observation as a sidecar on the same envelope — one
    // obligation, no duplicate wake. A malformed sidecar fails the whole
    // event closed rather than silently widening the admitted record.
    let sidecar = null;
    if (event.observation !== undefined && event.observation !== null) {
      const problem = observationProblem(event.observation);
      if (problem) {
        log(`unparseable whatsapp observation sidecar (${problem}), skipping`);
        return null;
      }
      sidecar = event.observation;
    }

    return {
      kind,
      upstream,
      observation: sidecar,
      transitions: observationTransitions(sidecar, conversationIdFor(chatJid, resolvedPhone)),
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
