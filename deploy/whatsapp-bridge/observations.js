/**
 * Typed platform observations for WhatsApp — the JS mirror of
 * `nora/domain/observations.py` (the header + payload union) and
 * `nora/platform/bridge/rehydrate.py:observation_to_dict` (the wire dict).
 *
 * Every function here is pure: Baileys stanzas/callback payloads in, routing
 * events out. Bridge.js subscribes the native surfaces and feeds these;
 * normalize.js validates the produced dict before capture. A source event
 * about an OBJECT (poll, room, member set, pin set, artifact) is never an
 * utterance — no fabricated speaker line, no fake empty message.
 *
 * Identity rules (design/platform-observations/02):
 *   - `upstream` is the transition identity for the envelope's source_key;
 *     stanza-bearing events use `s:{stanza}`-family forms, stanza-less
 *     callbacks a content hash that a redelivery reproduces byte-identically.
 *   - `object_ref` is the source's own object handle: `poll:{creation id}`,
 *     `room:{chat}`, `members:{chat}`, `pins:{chat}`, `artifact:{stanza}`.
 *   - `actor` is who acted per the source (`key.participant`, `author`,
 *     `actor_fbid`); `subject_ref` is who the event is ABOUT. A remove is
 *     never "left" — voluntary departure is not inferable.
 *   - `occurred_ms`/`source_position` exist only when the source supplies
 *     them; WA has no global ordering token, so revision fields stay null
 *     and arrival order is honestly labelled downstream.
 */
import { createHash } from 'node:crypto';
import {
  cacheEntryMatchesKey, getMessageContent, normalizeJid, resolveIdentity,
  timestampMs, toNumber,
} from './bridge_helpers.js';
import {
  conversationIdFor, isBroadcast, isGroupJid, isNewsletter, isPhoneJid,
  parseJid, senderForm,
} from './wa_jid.js';

export const OBSERVATION_CONTRACT = 1;

const KIND_OPERATIONS = {
  reaction: new Set(['add', 'remove', 'clear', 'snapshot']),
  message_metadata: new Set(['create', 'revise', 'withdraw']),
  poll: new Set(['create', 'revise', 'withdraw', 'add', 'remove', 'clear', 'snapshot']),
  room: new Set(['create', 'revise', 'withdraw', 'snapshot']),
  membership: new Set(['add', 'remove', 'revise', 'snapshot']),
  pin: new Set(['add', 'remove', 'snapshot']),
  artifact: new Set(['create', 'revise', 'withdraw']),
};
const ACTOR_STATUSES = new Set(['known', 'anonymous', 'not_supplied', 'not_applicable']);

/**
 * The `observation_from_dict` invariants the boundary checks, ported:
 * returns null when the dict is a legal wire observation, else a reason.
 * A malformed dict fails closed — it never reaches the journal as a
 * best-effort parse.
 */
export function observationProblem(obs) {
  if (!obs || typeof obs !== 'object') return 'not an object';
  if (obs.contract !== OBSERVATION_CONTRACT) return 'contract';
  const ops = KIND_OPERATIONS[obs.kind];
  if (!ops) return 'kind';
  if (!ops.has(obs.operation)) return 'operation';
  if (typeof obs.object_ref !== 'string' || !obs.object_ref) return 'object_ref';
  if (obs.actor_status !== undefined && !ACTOR_STATUSES.has(obs.actor_status)) return 'actor_status';
  if ((obs.actor_status || 'not_supplied') === 'known' && !obs.actor_id) return 'actor';
  if (obs.actor_id && obs.actor_status === 'not_applicable') return 'actor';
  if (obs.time_quality === 'upstream' && (typeof obs.occurred_ms !== 'number')) return 'occurred_ms';
  if (obs.time_quality !== undefined && !['upstream', 'bridge_local', 'unknown'].includes(obs.time_quality)) {
    return 'time_quality';
  }
  if (obs.source_position !== null && obs.source_position !== undefined && !obs.revision_scope) {
    return 'revision_scope';
  }
  const scope = obs.scope || {};
  for (const k of ['platform', 'account_key', 'conversation_id']) {
    if (typeof scope[k] !== 'string' || !scope[k]) return `scope.${k}`;
  }
  return null;
}

/** sha256 hex — deterministic option/vote refs and content identities. */
function sha256hex(buf) {
  return createHash('sha256').update(buf).digest('hex');
}

/** A stable content identity for stanza-less callbacks: sorted-JSON digest. */
export function contentHash(value) {
  return sha256hex(Buffer.from(JSON.stringify(sortDeep(value)), 'utf8')).slice(0, 20);
}

function sortDeep(v) {
  if (Array.isArray(v)) return v.map(sortDeep);
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, sortDeep(v[k])]));
  }
  return v;
}

function targetDict(messageId) {
  return { message_id: messageId, sent_ts: null, mentions_known: true };
}

/** The wire dict — `observation_to_dict` field-for-field. */
function observationDict({
  kind, scope, objectRef, operation, payload, target = null,
  targetAuthorId = '', subjectRef = '', actorId = '', actorStatus = 'not_supplied',
  occurredMs = null, timeQuality = 'unknown', sourceRevision = null,
  sourcePosition = null, revisionScope = '',
  coverage = { complete: true, covered: 'event', gaps: [], reason: '' },
}) {
  return {
    contract: OBSERVATION_CONTRACT,
    kind,
    scope,
    object_ref: objectRef,
    target,
    target_author_id: targetAuthorId,
    operation,
    subject_ref: subjectRef,
    actor_id: actorId,
    actor_status: actorStatus,
    occurred_ms: occurredMs,
    received_ms: 0, // stamped by capture, next to the envelope's received_ms
    time_quality: timeQuality,
    source_revision: sourceRevision,
    source_position: sourcePosition,
    revision_scope: revisionScope,
    coverage: {
      complete: coverage.complete !== false,
      covered: coverage.covered || 'event',
      gaps: coverage.gaps || [],
      reason: coverage.reason || '',
    },
    payload,
  };
}

/** Full poll payload — PollPayload.to_dict always emits every field. */
function pollPayload(over = {}) {
  return {
    question: null, options: [], multi_select: null, anonymous: null,
    state: '', close_ms: null, parent_ref: '', voter_ref: '', option_ref: '',
    selected: null, option_counts: {}, voter_count: null, voters: null,
    ...over,
  };
}

function roomPayload(fields) {
  return { fields };
}

function membershipPayload(over = {}) {
  return { role: '', state: '', covered: '', members: [], ...over };
}

function pinPayload(over = {}) {
  return { message: null, pins: [], ...over };
}

function artifactPayload(over = {}) {
  return {
    artifact_kind: '', metadata: {}, custody: 'declared',
    perception_ref: '', preview: false,
    ...over,
  };
}

/** sha256(optionName) hex — the upstream join key between vote hashes and
 * creation options (Baileys aggregates on sha256 of the option name).
 * optionHash, when the source supplies one, is the same digest — verbatim. */
function optionRef(option) {
  const hash = option?.optionHash;
  if (typeof hash === 'string' && hash) return hash;
  return sha256hex(Buffer.from(String(option?.optionName || ''), 'utf8'));
}

function nameRef(optionName) {
  return sha256hex(Buffer.from(String(optionName || ''), 'utf8'));
}

/** A vote hash (bytes|base64 string|Buffer) → the same ref shape optionRef emits. */
function voteHashRef(v) {
  if (Buffer.isBuffer(v) || v instanceof Uint8Array) return Buffer.from(v).toString('hex');
  const s = String(v || '');
  // protobufjs surfaces bytes as base64 strings; a plain hex string passes.
  if (/^[0-9a-fA-F]+$/.test(s) && s.length === 64) return s.toLowerCase();
  try {
    return Buffer.from(s, 'base64').toString('hex');
  } catch {
    return s;
  }
}

// -- shared context --------------------------------------------------------

function resolveId(jid, ctx) {
  const j = normalizeJid(jid);
  if (!j) return { jid: '', id: '' };
  const forms = resolveIdentity(j, ctx.lidToPhone, ctx.phoneToLid);
  return { jid: j, id: senderForm(j, forms.phone, forms.lid), forms };
}

/**
 * The envelope routing fields for a stanza in a chat: conversation id/type,
 * the actor in sender form, and the actor's honest status. A group stanza
 * with no participant names no actor — the room JID is not a member, so the
 * observation header records `not_supplied`, never the guessed room.
 */
function stanzaContext(msg, ctx) {
  const chatJid = normalizeJid(msg?.key?.remoteJid) || msg?.key?.remoteJid || '';
  const group = isGroupJid(chatJid);
  const actorJidRaw = group
    ? (msg.key?.participant || msg.key?.participantAlt || '')
    : msg.key?.remoteJid;
  const actor = actorJidRaw ? resolveId(actorJidRaw, ctx) : { jid: '', id: '' };
  // DM conversation id mirrors normalize.js exactly: the sender's resolved
  // phone first (a LID-JID DM resolves through the session map), else the
  // chat's own digits for a phone JID, else the raw JID — so a sidecar's
  // scope equals the message envelope's room verbatim.
  const parts = parseJid(chatJid);
  const resolvedPhone = group ? null
    : (actor.forms?.phone || (parts && isPhoneJid(chatJid) ? parts.user : null));
  return {
    conversationId: conversationIdFor(chatJid, resolvedPhone),
    conversationType: group ? 'group' : 'dm',
    actorId: actor.id,
    actorStatus: actor.id ? 'known' : 'not_supplied',
    pushName: msg.pushName || null,
  };
}

function scopeDict(ctx, conversationId) {
  return {
    platform: 'whatsapp', account_key: ctx.accountKey,
    conversation_id: conversationId, thread: '',
  };
}

function routed(ctx, sc, obs, upstream, { senderId = null, sentMs = null, timeQuality = 'unknown' } = {}) {
  return {
    type: 'observation',
    kind: obs.kind,
    upstream,
    observation: obs,
    conversation_id: sc.conversationId,
    conversation_type: sc.conversationType,
    sender_id: senderId === null ? sc.actorId : senderId,
    sender_name: sc.pushName,
    sent_ms: sentMs,
    time_quality: timeQuality,
  };
}

// -- stanzas → observation events ------------------------------------------

/**
 * One WAMessage stanza → zero or more observation events. Runs when
 * extractMessageEvent produced nothing (contentless objects, and stub
 * stanzas that carry no `message` at all). `ctx` supplies identity maps,
 * the recent-message cache (poll/event secrets live there under private
 * bridge custody) and the injected decrypt util — injected so this module
 * stays pure and testable.
 */
export function stanzaObservations(msg, ctx = {}) {
  if (!msg?.key?.id || !msg.key.remoteJid) return [];
  const chatJid = normalizeJid(msg.key.remoteJid) || msg.key.remoteJid;
  if (isBroadcast(chatJid) || isNewsletter(chatJid)) return [];
  const content = getMessageContent(msg) || {};
  const out = [];

  const poll = content.pollCreationMessage || content.pollCreationMessageV2
    || content.pollCreationMessageV3 || content.pollCreationMessageV5;
  if (poll) out.push(...pollCreation(msg, poll, ctx));

  if (content.pollUpdateMessage) out.push(...pollVoteUpdate(msg, content.pollUpdateMessage, ctx));

  const snap = content.pollResultSnapshotMessage || content.pollResultSnapshotMessageV3;
  if (snap) out.push(...pollSnapshot(msg, snap, ctx));

  if (content.pinInChatMessage) out.push(...pinInChat(msg, content.pinInChatMessage, ctx));

  if (content.contactMessage) {
    const one = content.contactMessage;
    out.push(...sharedContacts(msg, { displayName: one.displayName, contacts: [one] }, ctx));
  }
  if (content.contactsArrayMessage) out.push(...sharedContacts(msg, content.contactsArrayMessage, ctx));
  if (content.locationMessage) out.push(...sharedLocation(msg, content.locationMessage, ctx));
  if (content.eventMessage) out.push(...sharedEvent(msg, content.eventMessage, ctx));

  // Stub notifications carry no `message` — the type lives on the stanza.
  if (msg.messageStubType !== undefined && msg.messageStubType !== null) {
    out.push(...stubNotifications(msg, ctx));
  }
  return out;
}

function pollCreation(msg, poll, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const selectable = toNumber(poll.selectableOptionsCount);
  const occurred = timestampMs(msg.messageTimestamp);
  const obs = observationDict({
    kind: 'poll', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `poll:${stanza}`,
    target: targetDict(stanza),
    operation: 'create',
    payload: pollPayload({
      question: poll.name ?? null,
      options: (poll.options || []).map((o) => ({ ref: optionRef(o), label: String(o?.optionName || '') })),
      multi_select: selectable === null ? null : selectable > 1,
      // No anonymity flag exists on PollCreationMessage — protocol-wise the
      // votes name their voters once decrypted, so the poll is non-anonymous.
      anonymous: false,
      state: 'open',
      parent_ref: stanza,
    }),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: occurred ? 'upstream' : 'unknown',
    sourceRevision: stanza,
    coverage: { covered: 'definition' },
  });
  return [routed(ctx, sc, obs, `s:${stanza}:poll`, { sentMs: occurred, timeQuality: occurred ? 'upstream' : 'unknown' })];
}

/** JID candidates for a decrypt identity — primary form plus the alt/LID
 * forms the key derivation may have used (GCM tag validates each try). */
function jidCandidates(key, ctx) {
  const out = new Set();
  for (const raw of [key?.participant, key?.participantAlt, key?.remoteJid, key?.remoteJidAlt]) {
    const j = normalizeJid(raw);
    if (!j || isGroupJid(j)) continue;
    out.add(j);
    const forms = resolveIdentity(j, ctx.lidToPhone, ctx.phoneToLid);
    if (forms.phone) out.add(`${forms.phone}@s.whatsapp.net`);
    if (forms.lid) out.add(`${forms.lid}@lid`);
  }
  return [...out];
}

function pollVoteUpdate(msg, upd, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const creationKey = upd.pollCreationMessageKey || {};
  const pollId = creationKey.id;
  if (!pollId) return [];
  const occurred = toNumber(upd.senderTimestampMs) || timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  // Stanza ids are sender-chosen: the same creation id can exist in another
  // room under another creator. The secret read is therefore BOUND to the
  // creation key the update itself names — right room, right creator — and
  // a key that cannot prove its creator (a group creation key with no
  // participant) yields no secret rather than borrowing one from an
  // unrelated stanza: the vote lands as unavailable, never a wrong decrypt.
  const cached = ctx.recentCache?.getBound
    ? ctx.recentCache.getBound(pollId, (e) => cacheEntryMatchesKey(e, creationKey, ctx))
    : (ctx.recentCache ? ctx.recentCache.get(pollId) : null);
  const secret = cached?.message_secret || null;
  const vote = upd.vote || {};

  let selected = null;
  if (secret && vote.encPayload && vote.encIv && ctx.decryptPollVote) {
    const creators = jidCandidates(creationKey, ctx);
    const voters = jidCandidates(msg.key, ctx);
    outer: for (const pollCreatorJid of creators) {
      for (const voterJid of voters) {
        try {
          const pv = ctx.decryptPollVote(
            { encPayload: vote.encPayload, encIv: vote.encIv },
            { pollCreatorJid, pollMsgId: pollId, pollEncKey: secret, voterJid },
          );
          if (pv) {
            selected = (pv.selectedOptions || []).map(voteHashRef);
            break outer;
          }
        } catch { /* wrong identity pair — GCM tag rejects it safely */ }
      }
    }
  }

  const base = {
    kind: 'poll', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `poll:${pollId}`,
    target: targetDict(pollId),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
  };

  let obs;
  if (selected === null) {
    // The vote stanza arrived but its selection cannot be read: a missing
    // creation messageSecret (creation predates capture or fell out of
    // cache) or a failed decrypt. Recorded as an explicit unavailable
    // coverage observation — never a zero, never an empty selection.
    obs = observationDict({
      ...base,
      operation: 'revise',
      payload: pollPayload(),
      coverage: {
        complete: false, covered: 'vote',
        gaps: ['selection'],
        reason: secret ? 'poll_vote_undecryptable' : 'poll_secret_unavailable',
      },
    });
  } else {
    // selectedOptions is the voter's COMPLETE replacement set; [] is a
    // withdrawn vote, distinct from an absent update.
    obs = observationDict({
      ...base,
      operation: 'revise',
      payload: pollPayload({ voter_ref: sc.actorId, selected }),
      coverage: { covered: 'vote' },
    });
  }
  return [routed(ctx, sc, obs, `v:${pollId}:${stanza}`, { sentMs: occurred, timeQuality: timeQ })];
}

function pollSnapshot(msg, snap, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  // The snapshot names its poll through contextInfo.stanzaId when the
  // source supplies it; when it does not, the object ref falls back to the
  // snapshot's own stanza and coverage records the unbound reference.
  const pollId = snap.contextInfo?.stanzaId || null;
  const occurred = timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const votes = Array.isArray(snap.pollVotes) ? snap.pollVotes : [];
  const payload = votes.length
    ? pollPayload({
      option_counts: Object.fromEntries(
        votes.map((v) => [nameRef(v?.optionName), Math.max(0, toNumber(v?.optionVoteCount) || 0)])),
    })
    // A COMPLETE empty aggregate table: no recorded votes. `voters: {}` is
    // the covered empty voter set; voter_count 0 follows from it. This is
    // not "unknown options" — it is a covered empty set.
    : pollPayload({ voter_count: 0, voters: {} });
  const obs = observationDict({
    kind: 'poll', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `poll:${pollId || stanza}`,
    target: pollId ? targetDict(pollId) : null,
    operation: 'snapshot',
    payload,
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
    coverage: {
      covered: 'option_counts',
      gaps: pollId ? [] : ['poll_ref'],
      reason: pollId ? '' : 'result snapshot carries no creation key',
      complete: true,
    },
  });
  return [routed(ctx, sc, obs, `ps:${stanza}`, { sentMs: occurred, timeQuality: timeQ })];
}

function pinInChat(msg, pin, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const targetId = pin.key?.id;
  const t = pin.type;
  const op = (t === 1 || t === 'PIN_FOR_ALL') ? 'add'
    : (t === 2 || t === 'UNPIN_FOR_ALL') ? 'remove' : null;
  if (!op || !targetId) return [];
  const occurred = toNumber(pin.senderTimestampMs) || timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const obs = observationDict({
    kind: 'pin', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `pins:${sc.conversationId}`,
    target: targetDict(targetId),
    operation: op,
    payload: pinPayload({ message: targetDict(targetId) }),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
  });
  return [routed(ctx, sc, obs, `pin:${targetId}:${op}:${stanza}`, { sentMs: occurred, timeQuality: timeQ })];
}

/**
 * One vCard → its useful user-authored fields. Contact phones/emails/names
 * are shared contact CONTENT (a person chose to send this card), not the
 * platform's internal ids — JIDs never come from a vcard field anyway.
 * Parsed once here; the record keeps the structured fields, never the raw
 * vcard bytes. A field the parse cannot supply is simply absent.
 */
function parseVcard(vcard) {
  const out = { phones: [], emails: [], urls: [] };
  if (typeof vcard !== 'string' || !vcard.trim()) return out;
  // RFC 6350 escaping: \\ \, \; \n \N — unescape AFTER splitting on
  // UNESCAPED separators, so an escaped semicolon in a name never breaks
  // N/ORG's component split and an escaped comma stays a comma.
  // One left-to-right pass: a "\\\\" pair is consumed atomically (data), so
  // "\\n" never collapses into a newline; an unrecognized escape keeps its
  // backslash rather than guessing.
  const unesc = (v) => String(v).replace(/\\(.)/gs,
    (m, c) => (c === "n" || c === "N" ? " "
      : (c === "\\" || c === "," || c === ";" ? c : m)));
  // A separator is a ";" preceded by an EVEN count of backslashes (each
  // "\\" pair is data); an odd count escapes it.
  const splitN = (raw) => raw.split(/(?<!\\)(?:\\\\)*;/)
    .map((p) => unesc(p).trim());
  // Unfold continuation lines (RFC 6350: a line starting space/tab joins the
  // previous one), then read NAME[;params]:value.
  const lines = String(vcard).replace(/\r\n/g, '\n').replace(/\n[ \t]/g, '').split('\n');
  const cap = (list, v, n = 8) => { if (v && list.length < n) list.push(v.slice(0, 120)); };
  for (const line of lines) {
    const m = /^([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$/.exec(line.trim());
    if (!m) continue;
    const name = m[1].toUpperCase();
    const raw = m[3].trim();
    const value = unesc(raw).trim();
    if (!value) continue;
    if (name === 'FN') out.formatted_name ||= value.slice(0, 80);
    else if (name === 'N') {
      const parts = splitN(raw);
      const full = [parts[1], parts[0]].filter(Boolean).join(' ').trim();
      if (full && !out.name) out.name = full.slice(0, 80);
    } else if (name === 'ORG') {
      out.organization ||= splitN(raw).filter(Boolean).join(' ').slice(0, 80);
    } else if (name === 'TITLE') {
      out.title ||= value.slice(0, 80);
    } else if (name === 'TEL') cap(out.phones, value);
    else if (name === 'EMAIL') cap(out.emails, value);
    else if (name === 'URL') cap(out.urls, value);
  }
  return out;
}

function sharedContacts(msg, contacts, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const occurred = timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const list = Array.isArray(contacts.contacts) ? contacts.contacts : [];
  const parsed = [];
  let unparsed = 0;
  for (const c of list) {
    const fields = parseVcard(c?.vcard);
    if (!fields.formatted_name && !fields.name && !fields.phones.length && !fields.emails.length) {
      unparsed += 1;
      continue;
    }
    const entry = { display_name: String(c?.displayName || '') };
    for (const k of ['formatted_name', 'name', 'organization', 'title']) {
      if (fields[k]) entry[k] = fields[k];
    }
    for (const k of ['phones', 'emails', 'urls']) {
      if (fields[k].length) entry[k] = fields[k];
    }
    parsed.push(entry);
  }
  const metadata = {
    display_name: String(contacts.displayName || ''),
    contact_count: list.length,
  };
  if (parsed.length) metadata.contacts = parsed.slice(0, 8);
  if (parsed.length > 8) metadata.contacts_more = parsed.length - 8;
  if (unparsed) metadata.unparsed = unparsed;
  const obs = observationDict({
    kind: 'artifact', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `artifact:${stanza}`,
    operation: 'create',
    payload: artifactPayload({
      artifact_kind: 'contact',
      metadata,
      custody: 'declared',
    }),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
    coverage: {
      gaps: unparsed ? ['vcard'] : [],
      reason: unparsed ? `${unparsed} vcard(s) yielded no readable fields` : '',
    },
  });
  return [routed(ctx, sc, obs, `s:${stanza}:contact`, { sentMs: occurred, timeQuality: timeQ })];
}

function sharedLocation(msg, loc, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const occurred = timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const obs = observationDict({
    kind: 'artifact', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `artifact:${stanza}`,
    operation: 'create',
    payload: artifactPayload({
      artifact_kind: 'location',
      metadata: {
        name: String(loc.name || ''),
        address: String(loc.address || ''),
      },
      custody: 'declared',
    }),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
    // Sender-authored venue text is message content; the latitude/longitude
    // pair is a coordinate egress and is withheld, not stored.
    coverage: { gaps: ['coordinates'], reason: 'coordinate fields withheld by policy' },
  });
  return [routed(ctx, sc, obs, `s:${stanza}:location`, { sentMs: occurred, timeQuality: timeQ })];
}

function sharedEvent(msg, event, ctx) {
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const occurred = timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const metadata = {
    name: String(event.name || ''),
    description: String(event.description || ''),
    is_canceled: Boolean(event.isCanceled),
    extra_guests_allowed: Boolean(event.extraGuestsAllowed),
  };
  const start = toNumber(event.startTime);
  if (start !== null) metadata.start_time_s = start;
  const end = toNumber(event.endTime);
  if (end !== null) metadata.end_time_s = end;
  // The venue's sender-authored name/address is content; coordinates are
  // withheld below, never stored.
  if (event.location) {
    const locName = String(event.location.name || '');
    const locAddr = String(event.location.address || '');
    if (locName) metadata.location_name = locName;
    if (locAddr) metadata.address = locAddr;
  }
  const obs = observationDict({
    kind: 'artifact', scope: scopeDict(ctx, sc.conversationId),
    objectRef: `artifact:${stanza}`,
    operation: 'create',
    payload: artifactPayload({ artifact_kind: 'event', metadata, custody: 'declared' }),
    actorId: sc.actorId, actorStatus: sc.actorStatus,
    occurredMs: occurred, timeQuality: timeQ,
    sourceRevision: stanza,
    // joinLink is a join credential; location carries coordinates. Both
    // are withheld from the record rather than stored.
    coverage: {
      gaps: ['join_link', 'coordinates'],
      reason: 'join credentials and coordinates withheld by policy',
    },
  });
  return [routed(ctx, sc, obs, `s:${stanza}:event`, { sentMs: occurred, timeQuality: timeQ })];
}

// -- group stub notifications ------------------------------------------------

const STUB_GROUP_CHANGE_SUBJECT = 21;
const STUB_GROUP_CHANGE_ICON = 22;
const STUB_GROUP_CHANGE_INVITE_LINK = 23;
const STUB_GROUP_CHANGE_DESCRIPTION = 24;
const STUB_GROUP_CHANGE_RESTRICT = 25;
const STUB_GROUP_CHANGE_ANNOUNCE = 26;
const STUB_PARTICIPANT_ADD = 27;
const STUB_PARTICIPANT_REMOVE = 28;
const STUB_PARTICIPANT_PROMOTE = 29;
const STUB_PARTICIPANT_DEMOTE = 30;
const STUB_PARTICIPANT_INVITE = 31;
const STUB_PARTICIPANT_LEAVE = 32;
const STUB_GROUP_MEMBER_ADD_MODE = 171;
const STUB_MEMBERSHIP_JOIN_APPROVAL_MODE = 145;

/** A stub parameter names a subject JID — bare, or the {lid,pn} JSON
 * objects newer protocol revisions use. Both spellings resolve. */
function stubSubject(param, ctx) {
  if (param && typeof param === 'object') {
    const jid = param.jid || param.pn || param.lid || '';
    return jid ? resolveId(jid, ctx).id : '';
  }
  const raw = String(param || '');
  if (!raw) return '';
  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw);
      const jid = parsed?.jid || parsed?.pn || parsed?.lid || '';
      return jid ? resolveId(jid, ctx).id : '';
    } catch { /* fall through to the plain-JID form */ }
  }
  return resolveId(raw, ctx).id;
}

const STUB_ROOM_OPS = new Set([
  STUB_GROUP_CHANGE_SUBJECT, STUB_GROUP_CHANGE_ICON, STUB_GROUP_CHANGE_INVITE_LINK,
  STUB_GROUP_CHANGE_DESCRIPTION, STUB_GROUP_CHANGE_RESTRICT, STUB_GROUP_CHANGE_ANNOUNCE,
  STUB_GROUP_MEMBER_ADD_MODE, STUB_MEMBERSHIP_JOIN_APPROVAL_MODE,
]);
const STUB_MEMBER_ACTIONS = {
  [STUB_PARTICIPANT_ADD]: 'add',
  [STUB_PARTICIPANT_INVITE]: 'add',
  [STUB_PARTICIPANT_REMOVE]: 'remove',
  [STUB_PARTICIPANT_LEAVE]: 'leave',
  [STUB_PARTICIPANT_PROMOTE]: 'promote',
  [STUB_PARTICIPANT_DEMOTE]: 'demote',
};

function stubNotifications(msg, ctx) {
  if (!isGroupJid(normalizeJid(msg.key.remoteJid) || msg.key.remoteJid)) return [];
  const sc = stanzaContext(msg, ctx);
  const stanza = msg.key.id;
  const type = toNumber(msg.messageStubType);
  const params = Array.isArray(msg.messageStubParameters) ? msg.messageStubParameters : [];
  const occurred = timestampMs(msg.messageTimestamp);
  const timeQ = occurred ? 'upstream' : 'unknown';
  const out = [];

  if (STUB_ROOM_OPS.has(type)) {
    const fields = {};
    if (type === STUB_GROUP_CHANGE_SUBJECT) fields.title = String(params[0] || '');
    if (type === STUB_GROUP_CHANGE_DESCRIPTION) fields.topic = String(params[0] || '');
    if (type === STUB_GROUP_CHANGE_ICON) fields.icon_changed = true;
    if (type === STUB_GROUP_CHANGE_INVITE_LINK) fields.invite_link_changed = true;
    if (type === STUB_GROUP_CHANGE_RESTRICT) fields.restrict = params[0] === 'true' || params[0] === 'on' || params[0] === true;
    if (type === STUB_GROUP_CHANGE_ANNOUNCE) fields.announce = params[0] === 'true' || params[0] === 'on' || params[0] === true;
    if (type === STUB_GROUP_MEMBER_ADD_MODE) fields.member_add_mode = params[0] === 'all_member_add' || params[0] === true;
    if (type === STUB_MEMBERSHIP_JOIN_APPROVAL_MODE) fields.join_approval_mode = params[0] === 'on' || params[0] === true;
    if (!Object.keys(fields).length) return [];
    const obs = observationDict({
      kind: 'room', scope: scopeDict(ctx, sc.conversationId),
      objectRef: `room:${sc.conversationId}`,
      operation: 'revise',
      payload: roomPayload(fields),
      actorId: sc.actorId, actorStatus: sc.actorStatus,
      occurredMs: occurred, timeQuality: timeQ,
      sourceRevision: stanza,
    });
    out.push(routed(ctx, sc, obs, `s:${stanza}:room`, { sentMs: occurred, timeQuality: timeQ }));
    return out;
  }

  const action = STUB_MEMBER_ACTIONS[type];
  if (!action) return [];
  // LEAVE is the only stub whose subject defaults to the actor when the
  // source names none — the leaver's own stanza names the actor.
  const subjects = params.map((p) => stubSubject(p, ctx)).filter(Boolean);
  if (!subjects.length && action === 'leave' && sc.actorId) subjects.push(sc.actorId);
  subjects.forEach((subject, i) => {
    let op = 'revise';
    let role = '';
    let state = '';
    if (action === 'add') { op = 'add'; state = 'member'; }
    else if (action === 'remove') { op = 'remove'; }
    else if (action === 'leave') { op = 'remove'; state = 'left'; }
    else if (action === 'promote') { op = 'revise'; role = 'admin'; }
    else if (action === 'demote') { op = 'revise'; role = ''; }
    const obs = observationDict({
      kind: 'membership', scope: scopeDict(ctx, sc.conversationId),
      objectRef: `members:${sc.conversationId}`,
      operation: op,
      subjectRef: subject,
      payload: membershipPayload({ role, state }),
      actorId: action === 'leave' && subject === sc.actorId ? sc.actorId : sc.actorId,
      actorStatus: sc.actorStatus,
      occurredMs: occurred, timeQuality: timeQ,
      sourceRevision: stanza,
      coverage: { covered: 'subject' },
    });
    out.push(routed(ctx, sc, obs, `s:${stanza}:m${i}`, {
      sentMs: occurred, timeQuality: timeQ,
    }));
  });
  return out;
}

// -- event-callback surfaces -------------------------------------------------

const GROUP_FIELD_MAP = [
  ['subject', 'title'],
  ['desc', 'topic'],
  ['announce', 'announce'],
  ['restrict', 'restrict'],
  ['ephemeralDuration', 'ephemeral_duration'],
  ['memberAddMode', 'member_add_mode'],
  ['joinApprovalMode', 'join_approval_mode'],
  ['isCommunity', 'is_community'],
  ['isCommunityAnnounce', 'is_community_announce'],
  ['linkedParent', 'linked_parent'],
];

function groupFields(entry) {
  const fields = {};
  for (const [src, dst] of GROUP_FIELD_MAP) {
    if (entry[src] !== undefined) fields[dst] = entry[src];
  }
  if (entry.inviteCode !== undefined) fields.invite_link_changed = true;
  return fields;
}

/**
 * `groups.update` — Partial<GroupMetadata> deltas. No actor field exists on
 * the metadata itself; a stub-emitted update may carry `author` (rc13
 * process-message.js appends it) — taken when present, never invented.
 */
export function groupsUpdateObservations(entry, ctx = {}) {
  const room = normalizeJid(entry?.id) || entry?.id;
  if (!room || !isGroupJid(room)) return [];
  const fields = groupFields(entry);
  if (!Object.keys(fields).length) return [];
  const convId = conversationIdFor(room, null);
  const actor = resolveId(entry.author || entry.authorPn || '', ctx);
  const obs = observationDict({
    kind: 'room', scope: scopeDict(ctx, convId),
    objectRef: `room:${convId}`,
    operation: 'revise',
    payload: roomPayload(fields),
    actorId: actor.id, actorStatus: actor.id ? 'known' : 'not_supplied',
    timeQuality: 'unknown',
    coverage: { covered: 'fields' },
  });
  return [routed(ctx, {
    conversationId: convId, conversationType: 'group',
    actorId: actor.id, pushName: null,
  }, obs, `gu:${convId}:${contentHash({ fields, actor: actor.jid })}`,
  { senderId: actor.id, sentMs: null, timeQuality: 'unknown' })];
}

const PARTICIPANT_OPS = { add: 'add', remove: 'remove', promote: 'revise', demote: 'revise', modify: 'revise' };

/**
 * `group-participants.update` — {id, author, participants[], action}:
 * author is the ACTOR ('' = the source supplied none), each participant a
 * SUBJECT. `remove` never establishes voluntary departure.
 */
export function participantsUpdateObservations(evt, ctx = {}) {
  const room = normalizeJid(evt?.id) || evt?.id;
  const action = String(evt?.action || '');
  const op = PARTICIPANT_OPS[action];
  if (!room || !isGroupJid(room) || !op || !Array.isArray(evt.participants)) return [];
  const convId = conversationIdFor(room, null);
  const actor = resolveId(evt.author || evt.authorPn || '', ctx);
  const out = [];
  for (const p of evt.participants) {
    // Stub-derived participants are JSON.parse'd objects carrying lid/pn/
    // phoneNumber (rc13 process-message); metadata callbacks carry id/lid.
    const subject = resolveId(
      p?.id || p?.jid || p?.lid || p?.phoneNumber || p?.pn || '', ctx).id;
    if (!subject) continue;
    let role = '';
    let state = '';
    if (action === 'add') { state = 'member'; role = p?.admin || ''; }
    else if (action === 'promote') { role = 'admin'; }
    else if (action === 'demote') { role = ''; }
    else if (action === 'modify') { role = p?.admin || ''; }
    const obs = observationDict({
      kind: 'membership', scope: scopeDict(ctx, convId),
      objectRef: `members:${convId}`,
      operation: op,
      subjectRef: subject,
      payload: membershipPayload({ role, state }),
      actorId: actor.id, actorStatus: actor.id ? 'known' : 'not_supplied',
      timeQuality: 'unknown',
      coverage: { covered: 'subject' },
    });
    // No stanza or revision exists here: content identity is the honest
    // dedupe — an identical fact redelivered reproduces the key, a distinct
    // transition differs. Same subject+actor+action re-occurring with no
    // discriminator is one fact by the source's own evidence.
    const upstream = `gpu:${convId}:${action}:${subject}:${contentHash({ a: actor.jid || '', r: role })}`;
    out.push(routed(ctx, {
      conversationId: convId, conversationType: 'group',
      actorId: actor.id, pushName: null,
    }, obs, upstream, { senderId: actor.id, sentMs: null, timeQuality: 'unknown' }));
  }
  return out;
}

/**
 * `groups.upsert` — GroupMetadata[] snapshots. `participants[]` present is a
 * COVERED member set (omitted members are unknown, not absent); its absence
 * means this entry is a field update only. `size` vs listed count is the
 * completeness check.
 */
export function groupsUpsertObservations(entry, ctx = {}) {
  const room = normalizeJid(entry?.id) || entry?.id;
  if (!room || !isGroupJid(room)) return [];
  const convId = conversationIdFor(room, null);
  const out = [];
  const fields = groupFields(entry);
  if (Object.keys(fields).length) {
    const obs = observationDict({
      kind: 'room', scope: scopeDict(ctx, convId),
      objectRef: `room:${convId}`,
      operation: 'revise',
      payload: roomPayload(fields),
      actorStatus: 'not_supplied',
      timeQuality: 'unknown',
      coverage: { covered: 'fields' },
    });
    out.push(routed(ctx, {
      conversationId: convId, conversationType: 'group',
      actorId: '', pushName: null,
    }, obs, `gus:${convId}:fields:${contentHash(fields)}`, { senderId: '', timeQuality: 'unknown' }));
  }
  const participants = entry.participants;
  if (Array.isArray(participants)) {
    const members = [];
    for (const p of participants) {
      const subject = resolveId(p?.id || p?.jid || p?.lid || '', ctx).id;
      if (!subject) continue;
      members.push({
        subject,
        role: typeof p?.admin === 'string' && p.admin ? p.admin : '',
        state: 'member',
      });
    }
    const declared = toNumber(entry.size);
    const complete = declared === null || declared === participants.length;
    const obs = observationDict({
      kind: 'membership', scope: scopeDict(ctx, convId),
      objectRef: `members:${convId}`,
      operation: 'snapshot',
      payload: membershipPayload({
        covered: 'members',
        members: members.map((m) => ({ subject: m.subject, role: m.role, state: m.state })),
      }),
      actorStatus: 'not_applicable',
      timeQuality: 'unknown',
      coverage: {
        complete,
        covered: 'members',
        gaps: complete ? [] : ['members'],
        reason: complete ? '' : `source lists ${participants.length} of declared ${declared}`,
      },
    });
    out.push(routed(ctx, {
      conversationId: convId, conversationType: 'group',
      actorId: '', pushName: null,
    }, obs, `gus:${convId}:members:${contentHash(members)}`, { senderId: '', timeQuality: 'unknown' }));
  }
  return out;
}

const JOIN_REQUEST_STATES = {
  created: 'requested',
  revoked: 'withdrawn',
  rejected: 'rejected',
};

/**
 * `group.join-request` — {id, author, participant, participantPn, action,
 * method}: a membership-approval transition the source carries on
 * stub `GROUP_MEMBERSHIP_JOIN_APPROVAL_REQUEST_NON_ADMIN_ADD`. The action
 * literals are the source's own vocabulary; `created` lands as the pending
 * `requested` state, never a `member` claim (approval arrives separately
 * as a `group-participants.update` add).
 */
export function joinRequestObservations(evt, ctx = {}) {
  const room = normalizeJid(evt?.id) || evt?.id;
  const action = String(evt?.action || '');
  const state = JOIN_REQUEST_STATES[action];
  if (!room || !isGroupJid(room) || !state) return [];
  const convId = conversationIdFor(room, null);
  const subject = resolveId(evt.participant || evt.participantPn || '', ctx).id;
  if (!subject) return [];
  const actor = resolveId(evt.author || evt.authorPn || '', ctx);
  const obs = observationDict({
    kind: 'membership', scope: scopeDict(ctx, convId),
    objectRef: `members:${convId}`,
    operation: 'revise',
    subjectRef: subject,
    payload: membershipPayload({ state }),
    actorId: actor.id, actorStatus: actor.id ? 'known' : 'not_supplied',
    timeQuality: 'unknown',
    coverage: { covered: 'subject' },
  });
  const upstream = `gjr:${convId}:${action}:${subject}:${contentHash({ a: actor.jid || '', m: evt.method || '' })}`;
  return [routed(ctx, {
    conversationId: convId, conversationType: 'group',
    actorId: actor.id, pushName: null,
  }, obs, upstream, { senderId: actor.id, timeQuality: 'unknown' })];
}

/**
 * `messages.update` — the WebMessageInfo surfaces a stored message's update
 * can carry: decrypted `eventResponses` bound to the event-creation key
 * (process-message.js's live `decryptEventResponse` path), `pollUpdates`
 * the sync merge attaches to a creation (its live vote emit is commented
 * out upstream — the stanza path covers live votes), the `pinInChat`
 * field-54 pin annotation, and `pollAdditionalMetadata.pollInvalidated`.
 */
export function messagesUpdateObservations({ key, update } = {}, ctx = {}) {
  const chatJid = normalizeJid(key?.remoteJid) || key?.remoteJid || '';
  if (!chatJid || isBroadcast(chatJid) || isNewsletter(chatJid)) return [];
  const group = isGroupJid(chatJid);
  const convId = conversationIdFor(chatJid, group ? null : resolveIdentity(chatJid, ctx.lidToPhone, ctx.phoneToLid).phone);
  const sc = { conversationId: convId, conversationType: group ? 'group' : 'dm', actorId: '', pushName: null };
  const out = [];
  const stanza = key?.id || '';

  for (const pu of update?.pollUpdates || []) {
    const voter = resolveId(
      pu?.pollUpdateMessageKey?.participant || pu?.pollUpdateMessageKey?.remoteJid || '', ctx);
    const occurred = toNumber(pu?.senderTimestampMs) || toNumber(pu?.serverTimestampMs);
    const timeQ = occurred ? 'upstream' : 'unknown';
    const voteStanza = pu?.pollUpdateMessageKey?.id || contentHash(pu);
    const obs = observationDict({
      kind: 'poll', scope: scopeDict(ctx, convId),
      objectRef: `poll:${stanza}`,
      target: targetDict(stanza),
      operation: 'revise',
      payload: pollPayload({
        voter_ref: voter.id,
        selected: Array.isArray(pu?.vote?.selectedOptions)
          ? pu.vote.selectedOptions.map(voteHashRef) : [],
      }),
      actorId: voter.id, actorStatus: voter.id ? 'known' : 'not_supplied',
      occurredMs: occurred, timeQuality: timeQ,
      sourceRevision: voteStanza,
      coverage: { covered: 'vote' },
    });
    out.push(routed(ctx, sc, obs, `v:${stanza}:${voteStanza}`,
      { senderId: voter.id, sentMs: occurred, timeQuality: timeQ }));
  }

  if (update?.pollAdditionalMetadata?.pollInvalidated === true && stanza) {
    const obs = observationDict({
      kind: 'poll', scope: scopeDict(ctx, convId),
      objectRef: `poll:${stanza}`,
      target: targetDict(stanza),
      operation: 'revise',
      payload: pollPayload({ state: 'withdrawn' }),
      actorStatus: 'not_supplied',
      timeQuality: 'unknown',
      coverage: { covered: 'state' },
    });
    out.push(routed(ctx, sc, obs, `pi:${stanza}:${contentHash(update.pollAdditionalMetadata)}`,
      { senderId: '', timeQuality: 'unknown' }));
  }

  const pin = update?.pinInChat;
  if (pin?.key?.id) {
    const op = (pin.type === 1 || pin.type === 'PIN_FOR_ALL') ? 'add'
      : (pin.type === 2 || pin.type === 'UNPIN_FOR_ALL') ? 'remove' : null;
    if (op) {
      const occurred = toNumber(pin.senderTimestampMs) || toNumber(pin.serverTimestampMs);
      const timeQ = occurred ? 'upstream' : 'unknown';
      const obs = observationDict({
        kind: 'pin', scope: scopeDict(ctx, convId),
        objectRef: `pins:${convId}`,
        target: targetDict(pin.key.id),
        operation: op,
        payload: pinPayload({ message: targetDict(pin.key.id) }),
        actorStatus: 'not_supplied',
        occurredMs: occurred, timeQuality: timeQ,
        sourceRevision: stanza,
      });
      out.push(routed(ctx, sc, obs, `puc:${stanza}:${op}`,
        { senderId: '', sentMs: occurred, timeQuality: timeQ }));
    }
  }

  for (const er of update?.eventResponses || []) {
    const responder = resolveId(
      er?.eventResponseMessageKey?.participant || er?.eventResponseMessageKey?.remoteJid || '', ctx);
    const occurred = toNumber(er?.senderTimestampMs) || toNumber(er?.response?.timestampMs);
    const timeQ = occurred ? 'upstream' : 'unknown';
    const resp = er?.response?.response;
    const respName = typeof resp === 'string' ? resp.toLowerCase() : String(toNumber(resp) ?? '');
    const respStanza = er?.eventResponseMessageKey?.id || contentHash(er);
    // `response` folds per-responder on the actor — one responder's RSVP
    // never overwrites another's into a misleading last-response.
    const metadata = { response: respName };
    const guests = toNumber(er?.response?.extraGuestCount);
    if (guests !== null) metadata.extra_guest_count = guests;
    const obs = observationDict({
      kind: 'artifact', scope: scopeDict(ctx, convId),
      objectRef: `artifact:${stanza}`,
      target: targetDict(stanza),
      operation: 'revise',
      payload: artifactPayload({
        artifact_kind: 'event', metadata, custody: 'declared',
      }),
      actorId: responder.id, actorStatus: responder.id ? 'known' : 'not_supplied',
      occurredMs: occurred, timeQuality: timeQ,
      sourceRevision: respStanza,
      coverage: { covered: 'response' },
    });
    out.push(routed(ctx, sc, obs, `er:${stanza}:${respStanza}`, { senderId: responder.id, sentMs: occurred, timeQuality: timeQ }));
  }
  return out;
}
