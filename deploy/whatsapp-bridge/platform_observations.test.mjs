/**
 * Native platform-observation checks — the SUPPORTED capture each fixture
 * documents, exercised end to end at the boundary:
 *
 *   stanza/callback payload → observations.js → normalize.js:parseEvent
 *     → envelope (v2, typed observation, message:null for pure obs)
 *     → createPipeline (admit → fsync journal → drain → producer.enqueue)
 *
 * The poll-decrypt vector is built with Baileys' own proto + AES-GCM utils
 * (the inverse of decryptPollVote's derivation), so the decrypt path is
 * proven against the pinned library, not a stand-in.
 *
 * Run: node --test platform_observations.test.mjs
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync, mkdtempSync, rmSync } from 'node:fs';
import { createHash, createHmac, randomBytes, createCipheriv } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { decryptPollVote, proto } from '@whiskeysockets/baileys';

import { extractMessageEvent, createRecentMessageCache } from './bridge_helpers.js';
import {
  groupsUpsertObservations,
  groupsUpdateObservations,
  joinRequestObservations,
  messagesUpdateObservations,
  participantsUpdateObservations,
  stanzaObservations,
} from './observations.js';
import { parseEvent } from './normalize.js';
import { buildEnvelope } from './envelope.js';
import { Journal } from './journal.js';
import { StreamProducer, streamName } from './queue.js';
import { createPipeline } from './pipeline.js';

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'testdata', 'platform-observations');
const KEY = Buffer.alloc(32, 0xa1);
const GROUP = '12036123456789@g.us';

function fixture(name) {
  const doc = JSON.parse(readFileSync(path.join(DIR, name), 'utf8'));
  return doc.payload;
}

const fixtures = readdirSync(DIR).filter((f) => f.endsWith('.json'));
assert.ok(fixtures.length >= 15, 'fixture set unexpectedly small');

/** A recent cache containing the fixture's poll-creation stanza. */
function cacheWithPoll() {
  const cache = createRecentMessageCache(100);
  cache.remember(fixture('upsert-poll-creation.json'));
  return cache;
}

const baseCtx = {
  accountKey: 'acct-1', lidToPhone: {}, phoneToLid: {},
  recentCache: null, decryptPollVote,
};

// -- observation dict invariants (the wire contract) -------------------------

const KIND_OPS = {
  reaction: new Set(['add', 'remove', 'clear', 'snapshot']),
  message_metadata: new Set(['create', 'revise', 'withdraw']),
  poll: new Set(['create', 'revise', 'withdraw', 'add', 'remove', 'clear', 'snapshot']),
  room: new Set(['create', 'revise', 'withdraw', 'snapshot']),
  membership: new Set(['add', 'remove', 'revise', 'snapshot']),
  pin: new Set(['add', 'remove', 'snapshot']),
  artifact: new Set(['create', 'revise', 'withdraw']),
};

function assertWireObservation(obs, label) {
  assert.equal(obs.contract, 1, `${label}: contract`);
  assert.ok(KIND_OPS[obs.kind]?.has(obs.operation), `${label}: ${obs.kind}/${obs.operation}`);
  assert.equal(obs.scope.platform, 'whatsapp', `${label}: scope.platform`);
  assert.equal(obs.scope.account_key, 'acct-1', `${label}: scope.account_key`);
  assert.ok(obs.scope.conversation_id, `${label}: scope.conversation_id`);
  assert.ok(obs.object_ref, `${label}: object_ref`);
  assert.ok(['known', 'anonymous', 'not_supplied', 'not_applicable'].includes(obs.actor_status));
  if (obs.actor_status === 'known') assert.ok(obs.actor_id, `${label}: known actor needs id`);
  if (obs.time_quality === 'upstream') assert.equal(typeof obs.occurred_ms, 'number');
  assert.ok(obs.coverage && typeof obs.coverage.complete === 'boolean');
}

/** parsed = parseEvent(route, ...) → built envelope. */
function envelopeOf(route, extra = {}) {
  const parsed = parseEvent(route, {});
  assert.ok(parsed, 'parseEvent must accept the routed observation event');
  return buildEnvelope({
    platform: 'whatsapp', accountKey: 'acct-1',
    kind: parsed.kind, upstream: parsed.upstream,
    conversationId: parsed.route.conversation_id,
    conversationType: parsed.route.conversation_type,
    senderId: parsed.route.sender_id,
    senderName: parsed.route.sender_name,
    sentMs: parsed.route.sent_ms, receivedMs: 1782000000000, queuedMs: 1782000000000,
    timeQuality: parsed.route.time_quality === 'upstream' ? 'upstream' : 'bridge_local',
    message: null, observation: parsed.observation,
    attachments: [], trace: {}, receipt: null, admissionRev: 1,
    correlationKey: KEY, keyVersion: 1,
    ...extra,
  });
}

// -- stanzas → observation events ---------------------------------------------

test('every upsert payload is a WAMessage shape (key.remoteJid + key.id)', () => {
  for (const f of fixtures.filter((x) => x.startsWith('upsert-'))) {
    const doc = JSON.parse(readFileSync(path.join(DIR, f), 'utf8'));
    for (const msg of [].concat(doc.payload)) {
      assert.equal(typeof msg.key?.remoteJid, 'string', `${f}: no remoteJid`);
      assert.equal(typeof msg.key?.id, 'string', `${f}: no stanza id`);
    }
  }
});

test('poll creation stanza -> poll create observation carrying the definition', () => {
  const msg = fixture('upsert-poll-creation.json');
  const [ev] = stanzaObservations(msg, baseCtx);
  assert.ok(ev, 'poll creation must produce an observation');
  assertWireObservation(ev.observation, 'poll create');
  const o = ev.observation;
  assert.equal(o.kind, 'poll');
  assert.equal(o.operation, 'create');
  assert.equal(o.object_ref, 'poll:3EB0POLL01');
  assert.equal(o.target.message_id, '3EB0POLL01');
  assert.equal(o.payload.question, 'Saturday breakfast?');
  assert.deepEqual(
    o.payload.options.map((p) => p.label), ['Congee', 'Toast', 'Skip']);
  // Option refs are sha256(optionName) — the same join key decrypted votes
  // carry in selectedOptions.
  assert.equal(
    o.payload.options[0].ref,
    createHash('sha256').update('Congee', 'utf8').digest('hex'));
  assert.equal(o.payload.multi_select, false);
  assert.equal(o.payload.state, 'open');
  assert.equal(o.actor_status, 'known');
  assert.equal(o.actor_id, '+15550100011');
  assert.equal(o.time_quality, 'upstream');
  assert.equal(o.occurred_ms, 1781000000000);
  assert.equal(ev.upstream, 's:3EB0POLL01:poll');

  // Its envelope is a pure v2 observation: no fabricated utterance.
  const env = envelopeOf(ev);
  assert.equal(env.v, 2);
  assert.equal(env.kind, 'poll');
  assert.equal(env.message, null);
  assert.equal(env.observation.object_ref, 'poll:3EB0POLL01');
  assert.match(env.source_key, /\x1fpoll\x1fs:3EB0POLL01:poll$/);
});

test('the creation stanza seeds the secret custody that vote decryption needs', () => {
  const cache = createRecentMessageCache(100);
  cache.remember(fixture('upsert-poll-creation.json'));
  const cached = cache.get('3EB0POLL01');
  assert.ok(Buffer.isBuffer(cached.message_secret), 'creation secret must cache');
});

// A REAL encrypted vote, built as decryptPollVote's exact inverse:
//   key0  = HMAC-SHA256(key=32 zeros, msg=messageSecret)
//   decKey= HMAC-SHA256(key=key0, msg=msgId||creatorJid||voterJid||'Poll Vote'||0x01)
//   aad   = `${msgId}\u0000${voterJid}` ; enc = AES-256-GCM(decKey, iv, aad)
function encryptVote({ secret, pollMsgId, pollCreatorJid, voterJid, selected }) {
  const sign = Buffer.concat([
    Buffer.from(pollMsgId), Buffer.from(pollCreatorJid), Buffer.from(voterJid),
    Buffer.from('Poll Vote'), Buffer.from([1]),
  ]);
  const key0 = createHmac('sha256', Buffer.alloc(32)).update(secret).digest();
  const decKey = createHmac('sha256', key0).update(sign).digest();
  const aad = Buffer.from(`${pollMsgId}\u0000${voterJid}`);
  const plain = proto.Message.PollVoteMessage.encode(
    proto.Message.PollVoteMessage.create({ selectedOptions: selected })).finish();
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', decKey, iv);
  cipher.setAAD(aad);
  const enc = Buffer.concat([cipher.update(Buffer.from(plain)), cipher.final(), cipher.getAuthTag()]);
  return { encPayload: enc, encIv: iv };
}

test('an encrypted vote decrypts through the real util into a voter revise', () => {
  const creation = fixture('upsert-poll-creation.json');
  const cache = createRecentMessageCache(100);
  cache.remember(creation);
  const secret = cache.get('3EB0POLL01').message_secret;
  const congeeRef = createHash('sha256').update('Congee', 'utf8').digest();

  const msg = structuredClone(fixture('upsert-poll-update-encrypted.json'));
  msg.message.pollUpdateMessage.vote = encryptVote({
    secret, pollMsgId: '3EB0POLL01',
    pollCreatorJid: '15550100011@s.whatsapp.net',
    voterJid: '15550100022@s.whatsapp.net',
    selected: [congeeRef],
  });
  const [ev] = stanzaObservations(msg, { ...baseCtx, recentCache: cache });
  const o = ev.observation;
  assert.equal(o.operation, 'revise');
  assert.equal(o.object_ref, 'poll:3EB0POLL01');
  assert.equal(o.payload.voter_ref, '+15550100022');
  assert.deepEqual(o.payload.selected, [congeeRef.toString('hex')]);
  assert.equal(o.occurred_ms, 1790000700000);
  assert.equal(o.coverage.complete, true);
  assert.equal(ev.upstream, 'v:3EB0POLL01:3EB0VOTE01');
});

test('a vote whose creation secret is missing records explicit unavailable coverage', () => {
  const msg = fixture('upsert-poll-update-encrypted.json');
  // No creation in cache — the fixture's encPayload cannot decrypt either way.
  const [ev] = stanzaObservations(msg, baseCtx);
  const o = ev.observation;
  assert.equal(o.operation, 'revise');
  assert.equal(o.object_ref, 'poll:3EB0POLL01');
  assert.equal(o.payload.selected, null, 'never a guessed selection');
  assert.equal(o.payload.voter_ref, '');
  assert.equal(o.coverage.complete, false);
  assert.deepEqual(o.coverage.gaps, ['selection']);
  assert.equal(o.coverage.reason, 'poll_secret_unavailable');
  assert.equal(o.actor_id, '+15550100022', 'voter identity is still evidence');
});

test('aggregate result snapshots carry counts only — never voter identities', () => {
  const [ev] = stanzaObservations(fixture('upsert-poll-result-snapshot.json'), baseCtx);
  const o = ev.observation;
  assert.equal(o.operation, 'snapshot');
  assert.equal(o.coverage.covered, 'option_counts');
  const counts = o.payload.option_counts;
  assert.equal(Object.keys(counts).length, 3);
  const congee = counts[createHash('sha256').update('Congee', 'utf8').digest('hex')];
  assert.equal(congee, 3);
  assert.equal(o.payload.voter_count, null, 'no unique-voter total exists on this payload');
  assert.equal(o.payload.voters, null, 'aggregate counts never synthesize voters');
});

test('an empty pollVotes is a complete EMPTY covered set, not unknown options', () => {
  const [ev] = stanzaObservations(fixture('upsert-poll-result-snapshot-empty.json'), baseCtx);
  const o = ev.observation;
  assert.equal(o.operation, 'snapshot');
  assert.deepEqual(o.payload.voters, {}, 'covered empty voter set');
  assert.equal(o.payload.voter_count, 0);
  assert.equal(o.coverage.complete, true);
});

test('pin then unpin: per-transition ops, actor, upstream clock, target', () => {
  const [pin, unpin] = fixture('upsert-pin-unpin.json');
  const [pev] = stanzaObservations(pin, baseCtx);
  const [uev] = stanzaObservations(unpin, baseCtx);
  assert.equal(pev.observation.kind, 'pin');
  assert.equal(pev.observation.operation, 'add');
  assert.equal(uev.observation.operation, 'remove');
  assert.equal(pev.observation.object_ref, `pins:${GROUP}`);
  assert.equal(pev.observation.payload.message.message_id, '3EB0AAA100');
  assert.equal(pev.observation.target.message_id, '3EB0AAA100');
  assert.equal(pev.observation.actor_id, '+15550100011');
  assert.equal(pev.observation.occurred_ms, 1790001000000);
  assert.equal(pev.observation.time_quality, 'upstream');
  assert.notEqual(pev.upstream, uev.upstream, 'add/remove are distinct transitions');
  assert.equal(pev.upstream, 'pin:3EB0AAA100:add:3EB0PIN001');
  assert.equal(uev.upstream, 'pin:3EB0AAA100:remove:3EB0PIN002');
});

test('subject-change stub -> room revise with the new title, no utterance', () => {
  const msg = fixture('upsert-group-stub-subject.json');
  // The stub carries no `message` — extractMessageEvent cannot speak for it;
  // the observation path must produce the only record.
  assert.equal(extractMessageEvent(msg, {}), null);
  const [ev] = stanzaObservations(msg, baseCtx);
  const o = ev.observation;
  assert.equal(o.kind, 'room');
  assert.equal(o.operation, 'revise');
  assert.equal(o.object_ref, `room:${GROUP}`);
  assert.deepEqual(o.payload.fields, { title: 'Saturday planning' });
  assert.equal(o.actor_id, '+15550100011');
  const env = envelopeOf(ev);
  assert.equal(env.v, 2);
  assert.equal(env.kind, 'room');
  assert.equal(env.message, null);
});

test('participant-add stub -> membership add: subject != actor, state member', () => {
  const msg = fixture('upsert-group-stub-participant-add.json');
  const [ev] = stanzaObservations(msg, baseCtx);
  const o = ev.observation;
  assert.equal(o.kind, 'membership');
  assert.equal(o.operation, 'add');
  assert.equal(o.object_ref, `members:${GROUP}`);
  assert.equal(o.subject_ref, '+15550100044');
  assert.equal(o.actor_id, '+15550100011');
  assert.equal(o.payload.state, 'member');
});

test('shared objects -> artifact creates; custody declared, withheld fields are gaps', () => {
  const [contact, location, event] = fixture('upsert-shared-objects.json');
  const [cev] = stanzaObservations(contact, baseCtx);
  assert.equal(cev.observation.kind, 'artifact');
  assert.equal(cev.observation.payload.artifact_kind, 'contact');
  assert.equal(cev.observation.payload.metadata.display_name, 'Example Person');
  assert.equal(cev.observation.payload.custody, 'declared');
  assert.deepEqual(cev.observation.coverage.gaps, ['vcard']);
  const [lev] = stanzaObservations(location, baseCtx);
  assert.equal(lev.observation.payload.artifact_kind, 'location');
  assert.equal(lev.observation.payload.metadata.name, 'Example Cafe');
  assert.deepEqual(lev.observation.coverage.gaps, ['coordinates']);
  const [eev] = stanzaObservations(event, baseCtx);
  assert.equal(eev.observation.payload.artifact_kind, 'event');
  assert.equal(eev.observation.payload.metadata.name, 'Saturday breakfast');
  assert.deepEqual(eev.observation.coverage.gaps, ['join_link', 'coordinates']);
});

test('an ordinary text stanza produces no observation (unchanged v1 path)', () => {
  assert.deepEqual(stanzaObservations(fixture('upsert-message.json'), baseCtx), []);
  const ev = extractMessageEvent(fixture('upsert-message.json'), {});
  const parsed = parseEvent(ev, {});
  assert.equal(parsed.observation, null);
  assert.equal(parsed.kind, 'message');
});

test('a stanza that is both turn and object rides one envelope as a sidecar', () => {
  const msg = fixture('upsert-poll-creation.json');
  const [ev] = stanzaObservations(msg, baseCtx);
  // Simulate the bridge's sidecar attach: the ordinary event carries the
  // observation dict; the envelope must keep BOTH, no duplicate wake.
  const turn = {
    type: 'message', id: '3EB0TXT01', chat_jid: GROUP, is_group: true,
    from_me: false, sender_jid: '15550100011@s.whatsapp.net',
    sender_phone: '15550100011', sender_lid: null, push_name: 'Member A',
    text: 'vote above', timestamp_ms: 1781000000000,
    media: [], observation: ev.observation,
  };
  const parsed = parseEvent(turn, {});
  assert.equal(parsed.kind, 'message');
  assert.ok(parsed.observation, 'sidecar must ride the message envelope');
  const env = buildEnvelope({
    platform: 'whatsapp', accountKey: 'acct-1',
    kind: parsed.kind, upstream: parsed.upstream,
    conversationId: parsed.msg.conversation_id,
    conversationType: parsed.msg.conversation_type,
    senderId: parsed.msg.sender_id, senderName: parsed.msg.sender_name,
    sentMs: parsed.msg.timestamp, receivedMs: 1782000000000, queuedMs: 1782000000000,
    timeQuality: 'upstream', message: parsed.msg, observation: parsed.observation,
    attachments: [], trace: {}, receipt: null, admissionRev: 1,
    correlationKey: KEY, keyVersion: 1,
  });
  assert.equal(env.v, 2, 'message+observation is a v2 shape');
  assert.equal(env.kind, 'message', 'the utterance keeps its kind');
  assert.ok(env.message && env.message.body === 'vote above');
  assert.equal(env.observation.object_ref, 'poll:3EB0POLL01');
  // The scope binds to the SAME room as the utterance.
  assert.equal(env.observation.scope.conversation_id, env.conversation_id);
});

test('a malformed sidecar fails the whole event closed', () => {
  const turn = {
    type: 'message', id: '3EB0TXT02', chat_jid: GROUP, is_group: true,
    from_me: false, sender_jid: '15550100011@s.whatsapp.net',
    sender_phone: '15550100011', text: 'hi', timestamp_ms: 1781000000000,
    media: [], observation: { contract: 1, kind: 'poll', operation: 'explode' },
  };
  assert.equal(parseEvent(turn, {}), null);
});

// -- event-callback surfaces ---------------------------------------------------

test('groups.update entries -> room revise observations per changed field set', () => {
  const entries = fixture('event-groups-update.json');
  const events = entries.flatMap((e) => groupsUpdateObservations(e, baseCtx));
  assert.equal(events.length, 3);
  assert.deepEqual(events[0].observation.payload.fields, { title: 'Saturday planning' });
  assert.deepEqual(events[1].observation.payload.fields, { topic: 'Weekly breakfast coordination' });
  assert.deepEqual(events[2].observation.payload.fields, { announce: true });
  for (const ev of events) {
    assert.equal(ev.observation.actor_status, 'not_supplied');
    assert.equal(ev.conversation_id, GROUP);
    assert.ok(ev.upstream.startsWith(`gu:${GROUP}:`));
    assertWireObservation(ev.observation, 'room revise');
  }
});

test('group-participants.update -> subject deltas; remove is never a voluntary departure', () => {
  const [add, remove, promote, orphan] = fixture('event-group-participants-update.json');
  const [a] = participantsUpdateObservations(add, baseCtx);
  assert.equal(a.observation.operation, 'add');
  assert.equal(a.observation.subject_ref, '+15550100044');
  assert.equal(a.observation.actor_id, '+15550100011');
  assert.equal(a.observation.payload.state, 'member');
  const [r] = participantsUpdateObservations(remove, baseCtx);
  assert.equal(r.observation.operation, 'remove');
  assert.equal(r.observation.payload.state, '', 'kicked vs left-voluntarily is not inferable');
  const [p] = participantsUpdateObservations(promote, baseCtx);
  assert.equal(p.observation.operation, 'revise');
  assert.equal(p.observation.payload.role, 'admin');
  const [o] = participantsUpdateObservations(orphan, baseCtx);
  assert.equal(o.observation.actor_status, 'not_supplied', 'empty author is an honest unknown actor');
  assert.equal(o.observation.actor_id, '');
});

test('groups.upsert snapshot -> covered member set + room fields; partial entry lists none', () => {
  const [full, partial] = fixture('event-groups-upsert-snapshot.json');
  const fullEvents = groupsUpsertObservations(full, baseCtx);
  const roomObs = fullEvents.find((e) => e.observation.kind === 'room');
  const memberObs = fullEvents.find((e) => e.observation.kind === 'membership');
  assert.ok(roomObs && memberObs, 'a full snapshot yields both a room revise and a member snapshot');
  assert.equal(memberObs.observation.operation, 'snapshot');
  assert.equal(memberObs.observation.coverage.covered, 'members');
  assert.equal(memberObs.observation.coverage.complete, true, 'size 4 == 4 listed');
  const members = memberObs.observation.payload.members;
  assert.equal(members.length, 4);
  assert.equal(members.find((m) => m.subject === '+15550100011').role, 'superadmin');
  assert.equal(members.find((m) => m.subject === '+15550100033').role, '');
  assert.equal(memberObs.observation.actor_status, 'not_applicable', 'a set read names no actor');
  const partialEvents = groupsUpsertObservations(partial, baseCtx);
  assert.equal(partialEvents.length, 1);
  assert.equal(partialEvents[0].observation.kind, 'room', 'a field-only entry carries no member claim');
});

test('groups.upsert snapshot is partial when declared size exceeds the listing', () => {
  const meta = structuredClone(fixture('event-groups-upsert-snapshot.json')[0]);
  meta.size = 7; // source declares 7, lists 4 — a partial covered read.
  const memberObs = groupsUpsertObservations(meta, baseCtx)
    .find((e) => e.observation.kind === 'membership');
  assert.equal(memberObs.observation.coverage.complete, false);
  assert.deepEqual(memberObs.observation.coverage.gaps, ['members']);
});

test('join-request -> membership revise with the source\'s literal lifecycle state', () => {
  const [ev] = joinRequestObservations({
    id: GROUP, author: '15550100011@s.whatsapp.net',
    participant: '15550100055@s.whatsapp.net', action: 'created', method: 'invite_link',
  }, baseCtx);
  assert.equal(ev.observation.operation, 'revise');
  assert.equal(ev.observation.subject_ref, '+15550100055');
  assert.equal(ev.observation.payload.state, 'requested');
  assert.equal(ev.observation.actor_id, '+15550100011');
});

test('messages.update pollUpdates -> per-voter revises; empty selection is withdrawn', () => {
  const update = fixture('event-messages-update-poll.json');
  const events = messagesUpdateObservations(update, baseCtx);
  assert.equal(events.length, 2);
  const [v1, v2] = events;
  assert.equal(v1.observation.operation, 'revise');
  assert.equal(v1.observation.object_ref, 'poll:3EB0POLL01');
  assert.equal(v1.observation.payload.voter_ref, '+15550100022');
  assert.ok(Array.isArray(v1.observation.payload.selected));
  assert.equal(v1.observation.payload.selected.length, 1);
  assert.equal(v2.observation.payload.voter_ref, '+15550100033');
  assert.deepEqual(v2.observation.payload.selected, [], 'a complete empty selection = withdrawn vote');
  // Same upstream identity family as the live stanza path — the two surfaces
  // dedupe on the creation+vote-stanza pair, never double-count a vote.
  assert.equal(v1.upstream, 'v:3EB0POLL01:3EB0VOTE01');
});

// -- reaction transition identity ------------------------------------------------

test('add/remove/add on a reused stanza id stays three distinct transitions', () => {
  const seq = fixture('upsert-reaction-add-remove-add.json');
  const upstreams = seq.map((m) => parseEvent(extractMessageEvent(m, {}), {}).upstream);
  assert.equal(new Set(upstreams).size, 3,
    'the reaction source clock distinguishes a re-add from a replay');
  assert.match(upstreams[0], /:add:1790000200000$/);
  assert.match(upstreams[1], /:remove:1790000300000$/);
  assert.match(upstreams[2], /:add:1790000400000$/);
});

test('a reaction stanza with no source clock keeps its ambiguity honestly', () => {
  const [m] = [structuredClone(fixture('upsert-reaction-add.json'))];
  m.message.reactionMessage.senderTimestampMs = null;
  m.messageTimestamp = null;
  const parsed = parseEvent(extractMessageEvent(m, {}), {});
  assert.match(parsed.upstream, /:add:na$/, 'absent clock is :na — no fabricated discriminator');
});

// -- source -> queue proof --------------------------------------------------------

/**
 * The real capture path end to end: the observation event enters
 * `pipeline.capture`, admission admits the fixture room, the journal fsync
 * commits, the head-first drain publishes. The stream entry is the emitted
 * envelope — asserted byte-actual, not approximated.
 */
async function capturedEnvelopes(events, { room = GROUP } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), 'obsq-'));
  const redis = (() => {
    const store = new Map(); const stream = [];
    return {
      stream,
      async evalSha(_s, { keys, arguments: args }) {
        const orig = store.get(keys[1]);
        if (orig) return [store.get(keys[2] + orig), orig];
        const prior = store.get(keys[3]);
        if (prior) { store.set(keys[1], args[1]); return [prior, args[1]]; }
        const id = `1-${stream.length}`;
        stream.push({ id, fields: { e: args[0] } });
        store.set(keys[3], id); store.set(keys[1], args[1]);
        return [id, args[1]];
      },
      async eval(_s, args) { return this.evalSha(_s, args); },
    };
  })();
  try {
    const journal = new Journal(path.join(dir, 'capture'));
    const pipeline = createPipeline({
      platform: 'whatsapp', accountKey: 'acct-1',
      correlationKey: KEY, keyVersion: 1,
      journal,
      producer: new StreamProducer(redis, streamName('whatsapp', 'acct-1')),
      projection: {
        revision: 1, receiptEmoji: null,
        admitted: (cid) => cid === room,
        addressed: () => false,
      },
      attachments: { maxBytes: 1 << 20, putInbound: () => ({ key: 'k', size: 0 }) },
      downloadMedia: null, runReceipt: null, emitLegacy: null,
      metrics: null, jlog: () => {},
    });
    for (const ev of events) await pipeline.capture(ev, null);
    await pipeline.pump();
    return redis.stream.map((e) => JSON.parse(e.fields.e));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('poll stanza + group callback -> journaled and enqueued v2 observation envelopes', async () => {
  const events = [
    ...stanzaObservations(fixture('upsert-poll-creation.json'), baseCtx),
    ...participantsUpdateObservations(fixture('event-group-participants-update.json')[0], baseCtx),
    ...stanzaObservations(fixture('upsert-group-stub-subject.json'), baseCtx),
  ];
  const envs = await capturedEnvelopes(events);
  assert.equal(envs.length, 3);
  assert.equal(envs[0].v, 2);
  assert.equal(envs[0].kind, 'poll');
  assert.equal(envs[0].message, null);
  assert.equal(envs[0].observation.payload.question, 'Saturday breakfast?');
  assert.equal(envs[1].kind, 'membership');
  assert.equal(envs[1].observation.subject_ref, '+15550100044');
  assert.equal(envs[2].kind, 'room');
  // Distinct canonical source identities.
  assert.equal(new Set(envs.map((e) => e.source_key)).size, 3);
  // Admission stamps provenance; sender fields are the observation's actor.
  assert.equal(envs[1].admission_rev, 1);
  assert.equal(envs[1].sender_id, '+15550100011');
});

test('dedupe: a redelivered observation reproduces one stream entry', async () => {
  const events = [
    ...stanzaObservations(fixture('upsert-poll-creation.json'), baseCtx),
    ...stanzaObservations(fixture('upsert-poll-creation.json'), baseCtx),
  ];
  const envs = await capturedEnvelopes(events);
  assert.equal(envs.length, 1, 'identical source key enqueues once');
});

test('a denied room admits no observation at all', async () => {
  const events = stanzaObservations(fixture('upsert-poll-creation.json'), baseCtx);
  const envs = await capturedEnvelopes(events, { room: 'other@g.us' });
  assert.equal(envs.length, 0);
});
