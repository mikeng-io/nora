import { randomUUID } from 'node:crypto';
const generatedChallenge0 = randomUUID();
const generatedChallenge1 = randomUUID();
const generatedChallenge2 = randomUUID();
/**
 * /v1 surface tests: media custody, bridge_action reservation semantics
 * (fake pg that behaves like Postgres, including conflict rows), the
 * reserve→fence→execute→record pipeline, Baileys payload shapes, and the
 * capture pipeline end to end with fake Redis.
 *
 * Run: node --test v1.test.mjs
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { AttachmentStore, validAttachmentKey } from './media.js';
import { ActionStore, actionResponse } from './store.js';
import { ActionRejected, createV1, executors, recipientToJid, runAction } from './actions.js';
import { createPipeline } from './pipeline.js';
import { Projection } from './projection.js';
import { Journal } from './journal.js';
import { StreamProducer } from './queue.js';
import { Metrics } from './obs.js';
import { canonicalJson } from './canon.js';

const tmpDir = () => mkdtempSync(path.join(tmpdir(), 'wa-bridge-'));

// -- attachment custody ---------------------------------------------------------

test('attachment keys reject traversal and non-charset forms', () => {
  assert.equal(validAttachmentKey('out-act-1-0'), true);
  assert.equal(validAttachmentKey('a'.repeat(128)), true);
  assert.equal(validAttachmentKey('../secret'), false);
  assert.equal(validAttachmentKey('a/b'), false);
  assert.equal(validAttachmentKey('..'), false);
  assert.equal(validAttachmentKey('x'.repeat(129)), false);
});

test('custody is immutable: same bytes idempotent, different bytes refused', () => {
  const dir = tmpDir();
  try {
    const s = new AttachmentStore(dir);
    assert.equal(s.put('k1', Buffer.from('abc')), 'created');
    assert.equal(s.put('k1', Buffer.from('abc')), 'identical');
    assert.equal(s.put('k1', Buffer.from('xyz')), 'conflict');
    assert.deepEqual(s.read('k1'), Buffer.from('abc'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('reads never join untrusted path segments', () => {
  const dir = tmpDir();
  try {
    const s = new AttachmentStore(dir);
    assert.equal(s.read('../../etc/passwd'), null);
    assert.equal(s.read('..'), null);
    assert.throws(() => s.put('../x', Buffer.alloc(1)), /invalid attachment key/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('inbound custody keys are sha256[:32] content addresses', () => {
  const dir = tmpDir();
  try {
    const s = new AttachmentStore(dir);
    const { key, size } = s.putInbound(Buffer.from('media-bytes'));
    assert.match(key, /^[0-9a-f]{32}$/);
    assert.equal(size, 11);
    assert.ok(existsSync(path.join(dir, key)));
    // A second identical blob dedupes to the same key.
    assert.equal(s.putInbound(Buffer.from('media-bytes')).key, key);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('oversized inbound/outbound bodies are refused', () => {
  const dir = tmpDir();
  try {
    const s = new AttachmentStore(dir, { maxBytes: 8 });
    assert.throws(() => s.put('big', Buffer.alloc(9)), /over bound/);
    assert.throws(() => s.putInbound(Buffer.alloc(9)), /over bound/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

// -- bridge_action store (fake pg behaving like Postgres) -------------------------

function fakePg() {
  const actions = new Map();
  const works = new Map();   // id (string) → work row
  const events = new Map();  // id/digest/alias → {state, platform, account_key}
  const otps = new Map();    // challenge_id → {platform, expired}
  const messages = new Map(); // id → pending-outbound row
  function handle(sql, params = []) {
    if (/^INSERT INTO bridge_action/.test(sql)) {
      const [action_id, platform, account_key, owner_kind, owner_id, owner_generation, op, request, request_hash] = params;
      if (!actions.has(action_id)) {
        actions.set(action_id, {
          action_id, platform, account_key, owner_kind, owner_id, owner_generation,
          op, request, request_hash, state: 'prepared',
          platform_ref: null, platform_ms: null, error: null, message_id: null,
        });
      }
      return { rows: [], rowCount: 0 };
    }
    if (/^SELECT \* FROM bridge_action WHERE action_id = \$1( FOR UPDATE)?$/.test(sql)) {
      return { rows: actions.has(params[0]) ? [{ ...actions.get(params[0]) }] : [] };
    }
    if (/^SELECT \* FROM bridge_action\n?\s*WHERE action_id = \$1 AND platform = \$2 AND account_key = \$3/.test(sql)
        || /^SELECT \* FROM bridge_action WHERE action_id = \$1 AND platform = \$2 AND account_key = \$3/.test(sql)) {
      const row = actions.get(params[0]);
      const scoped = row && row.platform === params[1] && row.account_key === params[2];
      return { rows: scoped ? [{ ...row }] : [] };
    }
    if (/^SELECT state, generation, platform, account_key, conversation_id,\n?\s*payload, \(leased_until > now\(\)\) AS lease_live\n?\s*FROM bridge_work/.test(sql)) {
      const w = works.get(String(params[0]));
      return { rows: w ? [{ ...w }] : [] };
    }
    if (/^SELECT e\.state FROM bridge_event/.test(sql)) {
      // Any spelling — digest, direct id, live alias — resolves one row.
      const ev = events.get(params[0]);
      const scoped = ev && ev.platform === params[1] && ev.account_key === params[2];
      return { rows: scoped ? [{ state: ev.state }] : [] };
    }
    if (/^SELECT platform FROM otp_challenge/.test(sql)) {
      const ch = otps.get(params[0]);
      return { rows: ch && !ch.expired ? [{ platform: ch.platform }] : [] };
    }
    if (/^SELECT id FROM messages/.test(sql)) {
      const tagId = /tag_id' = \$\d/.test(sql) ? params[2] : null;
      const rowId = /AND id = \$\d/.test(sql) ? params[params.length - 1] : null;
      const rows = [...messages.values()].filter((m) =>
        m.is_nora === 1 && m.delivery_status === 'pending'
        && m.platform === params[0] && m.conversation_id === params[1]
        && (tagId == null || m.tag_id === tagId)
        && (rowId == null || String(m.id) === String(rowId)));
      return { rows: rows.map((m) => ({ id: m.id })) };
    }
    if (/SET state='rejected', error=\$2/.test(sql)) {
      const row = actions.get(params[0]);
      row.state = 'rejected';
      row.error = JSON.parse(params[1]);
      return { rows: [], rowCount: 1 };
    }
    if (/SET state='started', started_at=now\(\), message_id=\$2/.test(sql)) {
      const row = actions.get(params[0]);
      row.state = 'started';
      row.message_id = params[1] ?? null;
      return { rows: [], rowCount: 1 };
    }
    if (/^UPDATE bridge_action\n?\s*SET state=\$4/.test(sql)) {
      const [id, platform, account, state, ref, ms, error] = params;
      const row = actions.get(id);
      const ok = row && row.platform === platform && row.account_key === account
        && (row.state === 'prepared' || row.state === 'started');
      if (ok) {
        row.state = state;
        row.platform_ref = ref;
        row.platform_ms = ms;
        row.error = error ? JSON.parse(error) : null;
      }
      return { rows: [], rowCount: ok ? 1 : 0 };
    }
    if (/^(BEGIN|COMMIT|ROLLBACK)$/.test(sql)) return { rows: [] };
    throw new Error(`fake pg unhandled: ${sql.slice(0, 80)}`);
  }
  return {
    actions, works, events, otps, messages,
    query: handle,
    connect: async () => ({ query: handle, release() {} }),
  };
}

/** A running bridge_work row matching REQ's target on acct `accountKey`. */
function workRow(generation, { accountKey = 'a', conversationId = null, tagId = null } = {}) {
  return {
    state: 'running', generation, platform: 'whatsapp', account_key: accountKey,
    conversation_id: conversationId ?? REQ.target.id,
    payload: tagId ? { tag_id: tagId } : {},
    lease_live: true,
  };
}

const REQ = { target: { kind: 'dm', id: '85290000100@s.whatsapp.net' }, text: 'hi' };

test('reserve: fresh id executes, same request replays, different request conflicts', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'acct-1' });
  pg.works.set('1', workRow(3, { accountKey: 'acct-1' }));
  const owner = { kind: 'work', id: 'w-1', generation: 3 };

  const v1 = await store.reserve({ actionId: 'a1', owner, op: 'send', request: REQ });
  assert.equal(v1.verdict, 'execute');
  assert.equal(pg.actions.get('a1').state, 'started');   // may-have-happened marker

  const v2 = await store.reserve({ actionId: 'a1', owner, op: 'send', request: REQ });
  assert.equal(v2.verdict, 'replay');
  assert.equal(v2.row.state, 'started');                // NEVER re-executed

  const v3 = await store.reserve({ actionId: 'a1', owner, op: 'send', request: { ...REQ, text: 'different' } });
  assert.equal(v3.verdict, 'conflict');
});

test('reserve: identical request under a DIFFERENT immutable context conflicts', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'acct-1',
    receiptIntents: () => new Set(['e1']) });
  const owner = { kind: 'event_receipt', id: 'e1', generation: null };
  await store.reserve({ actionId: 'a2', owner, op: 'send', request: REQ });
  // Same action_id, same request bytes, different op → NOT a replay.
  const v = await store.reserve({ actionId: 'a2', owner, op: 'typing', request: REQ });
  assert.equal(v.verdict, 'conflict');
  const v2 = await store.reserve({ actionId: 'a2',
    owner: { kind: 'live_invocation', id: 'e1', generation: null }, op: 'send', request: REQ });
  assert.equal(v2.verdict, 'conflict');
});

test('work fence is inside reserve: stale generation rejects durably, no effect', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.works.set('1', workRow(5));                         // claimer bumped to 5
  const v = await store.reserve({ actionId: 'a3',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(v.verdict, 'replay');                     // recorded verdict
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.kind, 'fenced');
  // And a retry of the same action replays the recorded verdict again.
  const v2 = await store.reserve({ actionId: 'a3',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(v2.verdict, 'replay');
  assert.equal(v2.row.state, 'rejected');
});

test('work owner not in running state is fenced', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.works.set('1', { ...workRow(3), state: 'pending' });
  const v = await store.reserve({ actionId: 'a4',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.kind, 'fenced');
});

test('work target mismatch fences — the action cannot leave its work context', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.works.set('1', workRow(3, { conversationId: 'OTHER@g.us' }));
  const v = await store.reserve({ actionId: 'a5',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.kind, 'fenced');
});

test('unmappable work owner id fences honestly', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const v = await store.reserve({ actionId: 'a6',
    owner: { kind: 'work', id: 'not-a-work-id', generation: 1 }, op: 'send', request: REQ });
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.kind, 'fenced');
});

test('a lease-dead running work owner is fenced — the sweep need not have run', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.works.set('1', { ...workRow(3), lease_live: false });
  const v = await store.reserve({ actionId: 'a7',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(v.verdict, 'replay');
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.kind, 'fenced');
  assert.equal(v.row.error.reason, 'lease_expired');
});

test('event_receipt needs a JOURNALED intent — a minted id denies, a terminal event revokes', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a',
    receiptIntents: () => new Set(['v1:abc']) });
  // No journaled intent for 'ghost' — an HTTP caller cannot mint authority.
  const ghost = await store.reserve({ actionId: 'er-1',
    owner: { kind: 'event_receipt', id: 'ghost', generation: null }, op: 'react', request: REQ });
  assert.equal(ghost.row.state, 'rejected');
  assert.equal(ghost.row.error.reason, 'no_journaled_receipt_intent');
  // A real intent executes…
  const ok = await store.reserve({ actionId: 'er-2',
    owner: { kind: 'event_receipt', id: 'v1:abc', generation: null }, op: 'react', request: REQ });
  assert.equal(ok.verdict, 'execute');
  // …until a later-phase event row closes — revocation wins over the intent.
  pg.events.set('v1:abc', { state: 'done', platform: 'whatsapp', account_key: 'a' });
  const dead = await store.reserve({ actionId: 'er-3',
    owner: { kind: 'event_receipt', id: 'v1:abc', generation: null }, op: 'react', request: REQ });
  assert.equal(dead.row.state, 'rejected');
  assert.equal(dead.row.error.reason, 'receipt_event_terminal');
});

test('event_receipt without an intents collaborator denies, never fabricates', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const v = await store.reserve({ actionId: 'er-4',
    owner: { kind: 'event_receipt', id: 'v1:abc', generation: null }, op: 'react', request: REQ });
  assert.equal(v.row.state, 'rejected');
  assert.equal(v.row.error.reason, 'no_journaled_receipt_intent');
});

const CID = generatedChallenge0;

test('otp_challenge binds the real challenge row and the pending outbound row', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  // The app-side seam: live challenge + committed pending outbound row
  // tagged `otp-{id}` — the action's message_id links them.
  pg.otps.set(CID, { platform: 'whatsapp', expired: false });
  pg.messages.set(41, { id: 41, is_nora: 1, delivery_status: 'pending',
    platform: 'whatsapp', conversation_id: REQ.target.id, tag_id: `otp-${CID}` });
  const v = await store.reserve({ actionId: 'otp-1',
    owner: { kind: 'otp_challenge', id: CID, generation: null }, op: 'send', request: REQ });
  assert.equal(v.verdict, 'execute');
  assert.equal(pg.actions.get('otp-1').message_id, 41);
});

test('otp_challenge deny matrix: malformed, expired, foreign, missing pending row', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const cases = [
    ['malformed', { kind: 'otp_challenge', id: 'not-a-uuid', generation: null }],
    ['expired', { kind: 'otp_challenge', id: CID, generation: null }],
    ['foreign platform', { kind: 'otp_challenge', id: CID, generation: null }],
    ['no pending outbound', { kind: 'otp_challenge', id: CID, generation: null }],
  ];
  pg.otps.set(CID, { platform: 'whatsapp', expired: true });
  cases[1][1].id = CID;
  pg.otps.set(generatedChallenge1, { platform: 'discord', expired: false });
  cases[2][1].id = generatedChallenge1;
  pg.otps.set(generatedChallenge2, { platform: 'whatsapp', expired: false });
  cases[3][1].id = generatedChallenge2;
  for (const [i, [name, owner]] of cases.entries()) {
    const v = await store.reserve({ actionId: `otp-d-${i}`, owner, op: 'send', request: REQ });
    assert.equal(v.row.state, 'rejected', name);
    assert.equal(v.row.error.kind, 'fenced', name);
  }
});

test('an otp_challenge send rests REDACTED while the hash still fences codes', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.otps.set(CID, { platform: 'whatsapp', expired: false });
  pg.messages.set(41, { id: 41, is_nora: 1, delivery_status: 'pending',
    platform: 'whatsapp', conversation_id: REQ.target.id, tag_id: `otp-${CID}` });
  const owner = { kind: 'otp_challenge', id: CID, generation: null };
  await store.reserve({ actionId: 'otp-red',
    owner, op: 'send', request: { ...REQ, text: 'code 123456' } });
  // What rests is the redacted form — the live code never lands in JSONB.
  const row = pg.actions.get('otp-red');
  assert.equal(JSON.parse(row.request).text, '[redacted]');
  // The hash still binds the full request: a different code is a conflict.
  const c = await store.reserve({ actionId: 'otp-red', owner, op: 'send',
    request: { ...REQ, text: 'code 999999' } });
  assert.equal(c.verdict, 'conflict');
});

test('live_invocation binds the named pending row, scoped platform+conversation', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  pg.messages.set(7, { id: 7, is_nora: 1, delivery_status: 'pending',
    platform: 'whatsapp', conversation_id: REQ.target.id });
  const v = await store.reserve({ actionId: 'li-1',
    owner: { kind: 'live_invocation', id: '7', generation: null }, op: 'send', request: REQ });
  assert.equal(v.verdict, 'execute');
  assert.equal(pg.actions.get('li-1').message_id, 7);
  // A resolved or foreign row is not an owner.
  pg.messages.set(8, { id: 8, is_nora: 1, delivery_status: 'sent',
    platform: 'whatsapp', conversation_id: REQ.target.id });
  const done = await store.reserve({ actionId: 'li-2',
    owner: { kind: 'live_invocation', id: '8', generation: null }, op: 'send', request: REQ });
  assert.equal(done.row.error.reason, 'live_no_pending_outbound');
  const malformed = await store.reserve({ actionId: 'li-3',
    owner: { kind: 'live_invocation', id: 'app', generation: null }, op: 'send', request: REQ });
  assert.equal(malformed.row.error.reason, 'malformed_live_id');
});

test('GET and finish are scoped to platform+account — foreign rows invisible', async () => {
  const pg = fakePg();
  pg.actions.set('foreign', {
    action_id: 'foreign', platform: 'whatsapp', account_key: 'OTHER',
    owner_kind: 'work', owner_id: 'w-1', owner_generation: 1,
    op: 'send', request: REQ, request_hash: 'x', state: 'confirmed',
    platform_ref: 'S1', platform_ms: 1, error: null,
  });
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  assert.equal(await store.get('foreign'), null);        // scoped out
  // A finish against a row already terminal is a CAS no-op.
  pg.actions.set('done', {
    action_id: 'done', platform: 'whatsapp', account_key: 'a',
    owner_kind: 'work', owner_id: 'w-1', owner_generation: 1,
    op: 'send', request: REQ, request_hash: 'x', state: 'confirmed',
    platform_ref: 'S1', platform_ms: 1, error: null,
  });
  assert.equal(await store.finish('done', 'rejected', { error: { kind: 'x' } }), 0);
  assert.equal(pg.actions.get('done').state, 'confirmed');   // CAS held
});

// -- runAction ------------------------------------------------------------------

function fakeCtx() {
  const calls = [];
  const custody = new Map();
  const recent = new Map();
  return {
    calls, custody,
    sock: { sendPresenceUpdate: async (state, jid) => calls.push(['presence', state, jid]) },
    connected: () => true,
    sendWithTimeout: async (jid, payload, opts) => {
      calls.push(['send', jid, payload, opts]);
      return { key: { id: `S${calls.length}`, remoteJid: jid, fromMe: true },
        messageTimestamp: 1730300005 };
    },
    attachments: {
      read: (k) => custody.get(k) ?? null,
      maxBytes: 1 << 20,
    },
    // The cache's real interface is get/remember — a plain Map lies about it.
    recentMessages: {
      get: (k) => recent.get(k) ?? null,
      set: (k, v) => recent.set(k, v),
      remember(msg) { recent.set(msg?.key?.id, { raw: msg, key: msg?.key }); },
    },
    selfAliases: new Set(['+999']),
  };
}

test('runAction executes once and answers confirmed with the stanza ref', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const ctx = fakeCtx();
  pg.works.set('1', workRow(4));

  const res = await runAction({ store, ctx, actionId: 'a1',
    owner: { kind: 'work', id: 'w-1', generation: 4 }, op: 'send', request: REQ });
  assert.equal(res.state, 'confirmed');
  assert.equal(res.outcome, 'confirmed');
  assert.equal(res.platform_ref, 'S1');
  assert.equal(res.platform_ms, 1730300005000);
  assert.equal(ctx.calls.length, 1);
  assert.deepEqual(ctx.calls[0][1], '85290000100@s.whatsapp.net');
  assert.deepEqual(ctx.calls[0][2], { text: 'hi' });
});

test('a stalemated generation fence rejects before any upstream bytes', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const ctx = fakeCtx();
  pg.works.set('1', workRow(5));

  const res = await runAction({ store, ctx, actionId: 'a2',
    owner: { kind: 'work', id: 'w-1', generation: 3 }, op: 'send', request: REQ });
  assert.equal(res.state, 'rejected');
  assert.equal(res.error.kind, 'fenced');
  assert.equal(ctx.calls.length, 0);
});

test('unknown work id fences the same as a mismatch — never guessed', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const res = await runAction({ store, ctx: fakeCtx(), actionId: 'a3',
    owner: { kind: 'work', id: 'ghost', generation: 1 }, op: 'send', request: REQ });
  assert.equal(res.state, 'rejected');
  assert.equal(res.error.kind, 'fenced');
});

test('a started row is NEVER blindly retried — the replay answers uncertain', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const ctx = fakeCtx();
  pg.works.set('1', workRow(1));
  let effects = 0;
  ctx.sendWithTimeout = async () => { effects++; throw new Error('generated-private-sdk-detail'); };

  const first = await runAction({ store, ctx, actionId: 'a4',
    owner: { kind: 'work', id: 'w-1', generation: 1 }, op: 'send', request: REQ });
  assert.equal(first.state, 'uncertain');      // may have gone out
  assert.equal(first.outcome, 'uncertain');

  const second = await runAction({ store, ctx, actionId: 'a4',
    owner: { kind: 'work', id: 'w-1', generation: 1 }, op: 'send', request: REQ });
  assert.equal(second.state, 'uncertain');     // recorded state replayed
  assert.equal(second.outcome, 'uncertain');
  assert.equal(JSON.stringify([first, second]).includes('generated-private-sdk-detail'), false);
  assert.equal(effects, 1);
});

test('same id + different request conflicts without an upstream call', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const ctx = fakeCtx();
  pg.works.set('1', workRow(1));
  await runAction({ store, ctx, actionId: 'a5',
    owner: { kind: 'work', id: 'w-1', generation: 1 }, op: 'send', request: REQ });
  const res = await runAction({ store, ctx, actionId: 'a5',
    owner: { kind: 'work', id: 'w-1', generation: 1 }, op: 'send',
    request: { ...REQ, text: 'tampered' } });
  assert.equal(res.state, 'rejected');
  assert.equal(res.error.kind, 'conflict');
  assert.equal(ctx.calls.length, 1);
});

test('a disconnected socket answers unavailable BEFORE reserving a row', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' });
  const ctx = fakeCtx();
  ctx.connected = () => false;
  const res = await runAction({ store, ctx, actionId: 'a6',
    owner: { kind: 'event_receipt', id: 'e1', generation: null }, op: 'send', request: REQ });
  assert.equal(res.state, 'unavailable');
  assert.equal(res._status, 503);
  assert.equal(pg.actions.size, 0);             // no 'started' row pretending
});

test('a missing store answers unavailable, never an unreserved effect', async () => {
  const ctx = fakeCtx();
  const res = await runAction({ store: null, ctx, actionId: 'a7',
    owner: { kind: 'event_receipt', id: 'e1', generation: null }, op: 'send', request: REQ });
  assert.equal(res.state, 'unavailable');
  assert.equal(ctx.calls.length, 0);
});

test('validation failures are rejected (definitely not submitted)', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a',
    receiptIntents: () => new Set(['e1']) });
  const ctx = fakeCtx();
  const res = await runAction({ store, ctx, actionId: 'a8',
    owner: { kind: 'event_receipt', id: 'e1', generation: null }, op: 'send',
    request: { target: { kind: 'group', id: 'not-a-group' }, text: 'hi' } });
  assert.equal(res.state, 'rejected');
  assert.equal(res.error.kind, 'bad_request');
  assert.equal(ctx.calls.length, 0);
});

// -- executors: the real Baileys shapes -------------------------------------------

test('recipientToJid maps +phone → s.whatsapp.net, digits → lid', () => {
  assert.equal(recipientToJid('+85290000100'), '85290000100@s.whatsapp.net');
  assert.equal(recipientToJid('1234'), '1234@lid');
  assert.equal(recipientToJid('1@lid'), '1@lid');
  assert.equal(recipientToJid('+abc'), null);
  assert.equal(recipientToJid(''), null);
});

test('react builds Baileys {react:{text,key}} — removal sends empty text', async () => {
  const ctx = fakeCtx();
  await executors.react({
    target: { kind: 'group', id: '90000180@g.us' },
    to_message_id: '3EB0F0', to_author: '+85290000111', emoji: '👍', remove: false,
  }, ctx);
  const [, jid, payload] = ctx.calls[0];
  assert.equal(jid, '90000180@g.us');
  assert.equal(payload.react.text, '👍');
  assert.deepEqual(payload.react.key, {
    remoteJid: '90000180@g.us', id: '3EB0F0', fromMe: false,
    participant: '85290000111@s.whatsapp.net',
  });

  await executors.react({
    target: { kind: 'group', id: '90000180@g.us' },
    to_message_id: '3EB0F0', to_author: '+85290000111', emoji: '', remove: true,
  }, ctx);
  assert.equal(ctx.calls[1][2].react.text, '');
});

test('react reuses the cached stanza key when the target is still warm', async () => {
  const ctx = fakeCtx();
  const cachedKey = { remoteJid: '90000180@g.us', id: '3EB0F0', fromMe: false, participant: 'x@lid' };
  ctx.recentMessages.set('3EB0F0', { key: cachedKey });
  await executors.react({
    target: { kind: 'group', id: '90000180@g.us' },
    to_message_id: '3EB0F0', emoji: '👍',
  }, ctx);
  assert.equal(ctx.calls[0][2].react.key, cachedKey);   // object identity — the true key
});

test('edit builds Baileys {text, edit:{id,fromMe:true,remoteJid}}', async () => {
  const ctx = fakeCtx();
  await executors.edit({
    target: { kind: 'dm', id: '85290000100@s.whatsapp.net' },
    message_ref: '3EB0F0', text: 'corrected',
  }, ctx);
  const [, jid, payload] = ctx.calls[0];
  assert.equal(jid, '85290000100@s.whatsapp.net');
  assert.deepEqual(payload, {
    text: 'corrected',
    edit: { id: '3EB0F0', fromMe: true, remoteJid: '85290000100@s.whatsapp.net' },
  });
});

test('send attaches the caption once across multi-media payloads', async () => {
  const ctx = fakeCtx();
  ctx.custody.set('k-img', Buffer.from('img'));
  ctx.custody.set('k-doc', Buffer.from('doc'));
  await executors.send({
    target: { kind: 'dm', id: '85290000100@s.whatsapp.net' }, text: 'cap',
    attachments: [
      { key: 'k-img', media_type: 'image/jpeg' },
      { key: 'k-doc', media_type: 'application/pdf', file_name: 'f.pdf' },
    ],
  }, ctx);
  const [img, doc] = ctx.calls.map((c) => c[2]);
  assert.equal(img.image.toString(), 'img');
  assert.equal(img.caption, 'cap');
  assert.equal(doc.document.toString(), 'doc');
  assert.equal(doc.caption, undefined);          // caption is on ONE payload only
});

test('send with audio-only attachments sends text as its own stanza first', async () => {
  const ctx = fakeCtx();
  ctx.custody.set('k-a', Buffer.from('audio'));
  await executors.send({
    target: { kind: 'dm', id: '85290000100@s.whatsapp.net' }, text: 'note',
    attachments: [{ key: 'k-a', media_type: 'audio/mpeg' }],
  }, ctx);
  assert.deepEqual(ctx.calls[0][2], { text: 'note' });
  assert.deepEqual(ctx.calls[1][2].audio, Buffer.from('audio'));
});

test('a missing custody key is rejected, never an absent-blob send', async () => {
  const ctx = fakeCtx();
  await assert.rejects(() => executors.send({
    target: { kind: 'dm', id: '1@s.whatsapp.net' }, text: 'x',
    attachments: [{ key: 'ghost', media_type: 'image/png' }],
  }, ctx), ActionRejected);
  assert.equal(ctx.calls.length, 0);
});

test('typing hits sendPresenceUpdate, no stanza ref', async () => {
  const ctx = fakeCtx();
  const out = await executors.typing({ target: { kind: 'dm', id: '1@s.whatsapp.net' } }, ctx);
  assert.deepEqual(ctx.calls[0], ['presence', 'composing', '1@s.whatsapp.net']);
  assert.equal(out.platform_ref, null);
});

// -- projection -----------------------------------------------------------------

test('projection admits rooms and listed DM senders only', () => {
  const dir = tmpDir();
  try {
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({
      revision: 7, generated_ms: 1, enabled: true, account_key: 'acct-1',
      rooms: ['90000180@g.us'], dm_allowed_senders: ['+85290000100'],
      self_aliases: ['+999'], deterministic_triggers: ['nora'], receipt_emoji: '👀',
    }));
    const p = new Projection(dir, 'whatsapp');
    p._tick(true);
    assert.equal(p.revision, 7);
    assert.equal(p.admitted('90000180@g.us', '+anyone', 'group'), true);
    assert.equal(p.admitted('999@g.us', '+anyone', 'group'), false);
    assert.equal(p.admitted('85290000100@s.whatsapp.net', '+85290000100', 'dm'), true);
    assert.equal(p.admitted('1@s.whatsapp.net', '+stranger', 'dm'), false);
    assert.equal(p.receiptEmoji, '👀');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('disabled projection admits nothing', () => {
  const dir = tmpDir();
  try {
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({
      revision: 1, enabled: false, rooms: ['90000180@g.us'], dm_allowed_senders: [],
    }));
    const p = new Projection(dir, 'whatsapp');
    p._tick(true);
    assert.equal(p.admitted('90000180@g.us', '+x', 'group'), false);
    assert.equal(p.admitted('1@s.whatsapp.net', '+x', 'dm'), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('addressed: group receipts need sender grant AND deterministic addressed-ness', () => {
  const dir = tmpDir();
  try {
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({
      revision: 1, enabled: true, rooms: ['90000180@g.us'],
      dm_allowed_senders: ['+85290000100'],
      room_receipt_senders: {
        '90000180@g.us': ['+85290000100', '+85290000105'],
        '90000181@g.us': ['*'],
      },
      self_aliases: ['+999', '999'], deterministic_triggers: ['hey nora'],
      receipt_emoji: '👀',
    }));
    const p = new Projection(dir, 'whatsapp');
    p._tick(true);
    const g = (over) => ({ conversation_type: 'group', conversation_id: '90000180@g.us',
      sender_id: '+85290000100', mentions: [], quote: null, body: 'x', ...over });
    // Granted sender + each deterministic addressed form → receipt eligible.
    assert.equal(p.addressed(g({ mentions: ['+999'] })), true);
    assert.equal(p.addressed(g({ quote: { author_id: '+999' } })), true);
    assert.equal(p.addressed(g({ body: 'hey nora whats up' })), true);
    // Granted sender, NOT addressed → no receipt.
    assert.equal(p.addressed(g({ body: 'unrelated' })), false);
    // DENIED sender mentioning Nora in an allowed room → captured, no receipt.
    assert.equal(p.admitted('90000180@g.us', '+85290000106', 'group'), true);
    assert.equal(p.addressed(g({ sender_id: '+85290000106', mentions: ['+999'] })), false);
    // Room member in the map with NO dm grant → addressed → receipt.
    assert.equal(p.addressed(g({ sender_id: '+85290000105', mentions: ['+999'] })), true);
    // Unknown participation: no entry for the room → deny even when addressed.
    assert.equal(p.addressed(g({ conversation_id: '90000182@g.us', mentions: ['+999'] })), false);
    // Wildcard grant + addressed → receipt.
    assert.equal(p.addressed(g({ conversation_id: '90000181@g.us', sender_id: '+anyone', mentions: ['+999'] })), true);
    // Wildcard grant, still NOT addressed → no receipt.
    assert.equal(p.addressed(g({ conversation_id: '90000181@g.us', sender_id: '+anyone', body: 'unrelated' })), false);
    // DM is person-scoped by dm_allowed_senders — the group map never helps.
    assert.equal(p.addressed({ conversation_type: 'dm', conversation_id: 'x', sender_id: '+85290000100' }), true);
    assert.equal(p.addressed({ conversation_type: 'dm', conversation_id: 'x', sender_id: '+85290000105' }), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a projection with an out-of-range or missing revision is invalid', () => {
  const dir = tmpDir();
  try {
    for (const rev of [9007199254740992, -1, 'big', null, 1.5]) {
      writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({ revision: rev, enabled: true, rooms: ['r'] }));
      const p = new Projection(dir, 'whatsapp');
      p._tick(true);
      assert.equal(p.valid, false, `revision ${rev} must fail closed`);
    }
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({ revision: 9007199254740991, enabled: true, rooms: ['r'] }));
    const ok = new Projection(dir, 'whatsapp');
    ok._tick(true);
    assert.equal(ok.revision, 9007199254740991);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a missing projection file leaves last-good applied, marked stale', () => {
  const dir = tmpDir();
  try {
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({
      revision: 2, enabled: true, rooms: ['90000180@g.us'], dm_allowed_senders: [],
    }));
    const p = new Projection(dir, 'whatsapp');
    p._tick(true);
    rmSync(path.join(dir, 'whatsapp.json'));
    p._tick(false);
    assert.equal(p.stale, true);
    assert.equal(p.admitted('90000180@g.us', '+x', 'group'), true);  // last-good, never widened
    rmSync(dir, { recursive: true, force: true });
    const p2 = new Projection(dir, 'whatsapp');
    p2._tick(true);
    assert.equal(p2.valid, false);
    assert.equal(p2.admitted('90000180@g.us', '+x', 'group'), false); // fail closed
  } catch { /* cleanup best effort */ }
});

// -- /v1 HTTP surface ------------------------------------------------------------

function fakeReq(method, pathname, body = null) {
  const buf = body === null ? Buffer.alloc(0) : (Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body)));
  return {
    method, url: `http://b${pathname}`,
    on(ev, cb) {
      if (ev === 'data') setImmediate(() => cb(buf));
      if (ev === 'end') setImmediate(() => setImmediate(cb));
      return this;
    },
  };
}

function fakeRes() {
  return {
    status: null, json: null, body: null,
    writeHead(s) { this.status = s; return this; },
    end(b) { this.body = b; try { this.json = JSON.parse(b); } catch { this.json = null; } },
    get headersSent() { return this.status !== null; },
  };
}

test('HTTP errors retain outcomes without exposing exception details', async () => {
  const privateDetail = 'generated-private-detail\n at sdk (/private/generated/file.js:10)';
  const broken = () => { throw new Error(privateDetail); };
  const deps = {
    store: { reserve: broken, get: broken }, ctx: fakeCtx(),
    attachments: { put: broken }, health: () => ({}), self: () => null,
  };
  const v1 = createV1(deps);
  const cases = [
    ['POST', '/v1/actions', { action_id: 'error-case', owner: { kind: 'live_invocation', id: 'i1' },
      op: 'send', request: REQ }, 503],
    ['GET', '/v1/actions/error-case', null, 500],
    ['PUT', '/v1/attachments/key', Buffer.from('body'), 500],
    ['POST', '/v1/actions', Buffer.from('{"generated-private-detail":'), 400],
  ];
  for (const [method, pathname, body, status] of cases) {
    const res = fakeRes();
    await v1(fakeReq(method, pathname, body), res, new URL(`http://b${pathname}`));
    assert.equal(res.status, status);
    assert.ok(res.json.error);
    assert.equal(res.body.includes('generated-private-detail'), false);
    assert.equal(res.body.includes('/private/generated'), false);
    if (status === 503) assert.equal(res.json.state, 'unavailable');
  }
});

test('POST /v1/actions validates owner kinds before touching the store', async () => {
  const pg = fakePg();
  const v1 = createV1({
    store: new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a' }),
    ctx: fakeCtx(), attachments: null, self: () => null, health: () => ({}), metrics: null,
  });
  const res = fakeRes();
  const url = new URL('http://b/v1/actions');
  await v1(fakeReq('POST', '/v1/actions', {
    action_id: 'a1', owner: { kind: 'bogus', id: 'x' }, op: 'send', request: REQ,
  }), res, url);
  assert.equal(res.status, 400);
  assert.equal(pg.actions.size, 0);
});

test('unsupported ops are honest rejections, never 500s', async () => {
  const v1 = createV1({
    store: new ActionStore(fakePg(), { platform: 'whatsapp', accountKey: 'a' }),
    ctx: fakeCtx(), attachments: null, self: () => null, health: () => ({}), metrics: null,
  });
  const res = fakeRes();
  await v1(fakeReq('POST', '/v1/actions', {
    action_id: 'a1', owner: { kind: 'live_invocation', id: 'i1' }, op: 'teleport', request: {},
  }), res, new URL('http://b/v1/actions'));
  assert.equal(res.status, 200);
  assert.equal(res.json.outcome, 'unsupported');
});

test('GET /v1/actions/{id} returns the recorded row or 404', async () => {
  const pg = fakePg();
  const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'a',
    receiptIntents: () => new Set(['e1']) });
  const ctx = fakeCtx();
  await runAction({ store, ctx, actionId: 'a9',
    owner: { kind: 'event_receipt', id: 'e1', generation: null }, op: 'send', request: REQ });
  const v1 = createV1({ store, ctx, attachments: null, self: () => null, health: () => ({}), metrics: null });
  const hit = fakeRes();
  await v1(fakeReq('GET', '/v1/actions/a9'), hit, new URL('http://b/v1/actions/a9'));
  assert.equal(hit.json.state, 'confirmed');
  const miss = fakeRes();
  await v1(fakeReq('GET', '/v1/actions/zzz'), miss, new URL('http://b/v1/actions/zzz'));
  assert.equal(miss.status, 404);
});

test('PUT/GET /v1/attachments round-trips bytes; conflict answers 409', async () => {
  const dir = tmpDir();
  try {
    const v1 = createV1({
      store: null, ctx: fakeCtx(), attachments: new AttachmentStore(dir),
      self: () => null, health: () => ({}), metrics: null,
    });
    const put = fakeRes();
    await v1(fakeReq('PUT', '/v1/attachments/out-a-0', Buffer.from('png-bytes')),
      put, new URL('http://b/v1/attachments/out-a-0'));
    assert.equal(put.status, 201);
    const get = fakeRes();
    await v1(fakeReq('GET', '/v1/attachments/out-a-0'), get, new URL('http://b/v1/attachments/out-a-0'));
    assert.deepEqual(get.body, Buffer.from('png-bytes'));
    const conflict = fakeRes();
    await v1(fakeReq('PUT', '/v1/attachments/out-a-0', Buffer.from('other')),
      conflict, new URL('http://b/v1/attachments/out-a-0'));
    assert.equal(conflict.status, 409);
    const bad = fakeRes();
    // `..` must be percent-encoded or the URL parser collapses it pre-route.
    await v1(fakeReq('GET', '/v1/attachments/..%2F..'), bad, new URL('http://b/v1/attachments/..%2F..'));
    assert.equal(bad.status, 404);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

// -- capture pipeline end-to-end -------------------------------------------------

function fakeProjection() {
  return {
    revision: 9,
    admitted: (conv, sender, type) => conv === '90000180@g.us' || (type === 'dm' && sender === '+85290000100'),
    addressed: (msg) => msg.conversation_type === 'dm' || (msg.mentions || []).includes('+999'),
    receiptEmoji: '👀',
  };
}

function captureDeps(dir) {
  const journal = new Journal(path.join(dir, 'journal.log'));
  const stream = [];
  const dedupe = new Map();       // event_id → streamId
  const srcMap = new Map();       // source_key → canonical event_id
  const producer = {
    async enqueue(env) {
      // Contract semantics: source-key-first, then alias dedupe; returns
      // {stream_id, canonical_event_id}.
      const orig = srcMap.get(env.source_key);
      if (orig && dedupe.has(orig)) return { streamId: dedupe.get(orig), eventId: orig };
      if (dedupe.has(env.event_id)) {
        srcMap.set(env.source_key, env.event_id);
        return { streamId: dedupe.get(env.event_id), eventId: env.event_id };
      }
      const id = `1-${stream.length}`;
      stream.push(JSON.parse(JSON.stringify(env)));
      dedupe.set(env.event_id, id);
      srcMap.set(env.source_key, env.event_id);
      return { streamId: id, eventId: env.event_id };
    },
  };
  const receipts = [];
  return {
    journal, stream, dedupe, srcMap, receipts,
    deps: {
      platform: 'whatsapp', accountKey: 'acct-1',
      correlationKey: Buffer.alloc(32, 0xa1), keyVersion: 1,
      journal, producer,
      projection: fakeProjection(),
      attachments: new AttachmentStore(path.join(dir, 'att')),
      downloadMedia: async () => Buffer.from('blob'),
      runReceipt: async ({ event_id, request }) => {
        receipts.push({ event_id, request });
        return { state: 'confirmed' };
      },
      metrics: new Metrics(),
      jlog: () => {},
    },
  };
}

const EVENT = {
  type: 'message', id: '3EB0F1', chat_jid: '90000180@g.us', is_group: true,
  from_me: false, sender_jid: '85290000100@s.whatsapp.net',
  sender_phone: '+85290000100', sender_lid: null, push_name: null,
  text: 'hello', timestamp_ms: 1730300000000,
  mentioned: [{ phone: '+999', lid: null, jid: '999@s.whatsapp.net' }],
  quoted: null,
  media: [{ kind: 'image', mimetype: 'image/jpeg', voice_note: false, seconds: null,
    size: 4, caption: null, filename: null }],
  edit_of: null,
};

test('capture: denied rooms produce no journal row, no bytes, no enqueue', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    await p.capture({ ...EVENT, chat_jid: 'denied@g.us' }, {});
    assert.equal(journal.recover().length, 0);
    assert.equal(stream.length, 0);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('capture: admitted events journal, enqueue, and send the queued receipt', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, receipts, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();                       // capture returns at fsync; the drain publishes
    const env = stream[0];
    assert.equal(env.platform, 'whatsapp');
    assert.equal(env.account_key, 'acct-1');
    assert.equal(env.kind, 'message');
    assert.equal(env.admission_rev, 9);
    assert.match(env.event_id, /^v1:[0-9a-f]{64}$/);
    assert.equal(env.message.media[0].platform_id.length, 32);  // custody key landed
    assert.equal(env.attachments[0].media_type, 'image/jpeg');
    // Journal: envelope + enqueued + receipted — all three phases settled.
    const records = journal.recover();
    assert.equal(records.filter((r) => r.envelope).length, 1);
    assert.equal(records.filter((r) => r.enqueued).length, 1);
    assert.equal(records.filter((r) => r.receipted).length, 1);
    const plan = journal.plan(records);
    assert.equal(plan.pendingEnqueues + plan.pendingReceipts, 0);
    // Receipt fired with the deterministic react request under the alias.
    assert.equal(receipts.length, 1);
    assert.equal(receipts[0].event_id, env.event_id);
    assert.equal(receipts[0].request.emoji, '👀');
    assert.equal(receipts[0].request.to_message_id, '3EB0F1');
    assert.equal(env.receipt.emoji, '👀');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('no receipt is sent for unaddressed group messages', async () => {
  const dir = tmpDir();
  try {
    const { stream, receipts, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    await p.capture({ ...EVENT, mentioned: [] }, {});
    await p.replay();
    assert.equal(stream.length, 1);
    assert.equal(stream[0].receipt, null);
    assert.equal(receipts.length, 0);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('redis-down events stay journaled and replay enqueues exactly once', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, receipts, deps } = captureDeps(dir);
    let down = true;
    deps.producer = {
      async enqueue(env) {
        if (down) throw new Error('ECONNREFUSED');
        const id = `1-${stream.push(env) - 1}`;
        return { streamId: id, eventId: env.event_id };
      },
    };
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();                       // the drain fails the head enqueue
    assert.equal(stream.length, 0);
    assert.equal(journal.plan(journal.recover()).pendingEnqueues, 1);
    assert.equal(receipts.length, 0);               // NO receipt without enqueue
    down = false;
    await p.replay();
    assert.equal(stream.length, 1);
    const plan = journal.plan(journal.recover());
    assert.equal(plan.pendingEnqueues + plan.pendingReceipts, 0);
    assert.equal(receipts.length, 1);
    // A second replay is a no-op — the pending set is empty.
    await p.replay();
    assert.equal(stream.length, 1);
    assert.equal(receipts.length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a later capture never overtakes an older journaled head', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, deps } = captureDeps(dir);
    let down = true;
    const published = [];
    deps.producer = {
      async enqueue(env) {
        if (down) throw new Error('ECONNREFUSED');
        const id = `1-${stream.push(env) - 1}`;
        published.push(env.message.body);
        return { streamId: id, eventId: env.event_id };
      },
    };
    const p = createPipeline(deps);
    // Two captures while Redis is down — both journal, neither publishes.
    await p.capture(EVENT, {});
    await p.capture({ ...EVENT, id: '3EB0F2', text: 'second' }, {});
    await p.replay();
    assert.equal(stream.length, 0);
    assert.equal(journal.plan(journal.recover()).pendingEnqueues, 2);
    // Recovery: head-first — the first capture MUST publish before the second.
    down = false;
    await p.replay();
    assert.deepEqual(published.map((b) => b.split(' ')[0]), ['hello', 'second']);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a crash between enqueue and receipt re-runs ONLY the receipt', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, receipts, deps } = captureDeps(dir);
    let crash = true;
    deps.runReceipt = async ({ event_id, request }) => {
      if (crash) throw new Error('crash before receipt resolves');
      receipts.push({ event_id, request });
      return { state: 'confirmed' };
    };
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();                       // enqueue landed; receipt attempt crashed
    assert.equal(stream.length, 1);
    let plan = journal.plan(journal.recover());
    assert.equal(plan.pendingEnqueues, 0);
    assert.equal(plan.pendingReceipts, 1);  // the owed receipt survives
    // Simulate restart: a NEW pipeline over the same journal.
    crash = false;
    const p2 = createPipeline({ ...deps, journal });
    await p2.replay();
    assert.equal(stream.length, 1);         // NOT re-enqueued
    assert.equal(receipts.length, 1);       // the pending receipt ran
    assert.equal(receipts[0].event_id, stream[0].event_id);
    plan = journal.plan(journal.recover());
    assert.equal(plan.pendingReceipts, 0);  // receipted marker landed
    assert.equal(journal.recover().filter((r) => r.receipted).length, 1);
    // Third party: a later replay is a no-op — receipted is durable.
    await p2.replay();
    assert.equal(receipts.length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a rotated-alias replay binds the ORIGINAL event id for the receipt', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, receipts, deps, srcMap, dedupe } = captureDeps(dir);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    assert.equal(stream.length, 1);
    const origAlias = stream[0].event_id;
    assert.equal(receipts[0].event_id, origAlias);

    // Key rotation: a SECOND pipeline (keyVersion 2) re-captures the same
    // transition — same source_key, new alias. The producer resolves to the
    // original stream entry; the receipt/action owner stays the ORIGINAL id.
    const p2 = createPipeline({ ...deps, keyVersion: 2 });
    await p2.capture(EVENT, {});
    await p2.replay();
    assert.equal(stream.length, 1);                    // still one stream entry
    const markers = journal.recover().filter((r) => r.enqueued);
    const rotated = markers.find((m) => m.enqueued.orig_event_id === origAlias
      && m.enqueued.event_id !== origAlias);
    assert.ok(rotated, 'the rotated capture recorded orig_event_id');
    // The rotated envelope's receipt is owed under the ORIGINAL alias —
    // runReceipt received the canonical id, never v2's.
    assert.equal(receipts.filter((r) => r.event_id === origAlias).length, 2);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('re-delivery of the same stanza dedupes on event_id at the stream', async () => {
  const dir = tmpDir();
  try {
    const { stream, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.capture(EVENT, {});                    // Baileys upsert replay
    await p.replay();
    assert.equal(stream.length, 1);                // one entry, two journal rows
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a stanza-less reaction/message is retained ambiguous, never queued', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    // A reaction whose stanza carries no id has no stable transition
    // identity — a guess would collapse two distinct touches into one.
    await p.capture({
      type: 'reaction', id: '', chat_jid: '90000180@g.us', is_group: true,
      from_me: false, sender_jid: '85290000100@s.whatsapp.net',
      sender_phone: '+85290000100', target_id: '3EB0F0',
      emoji: '👍', removed: false, timestamp_ms: 1730300000000,
    }, {});
    await p.replay();
    assert.equal(stream.length, 0);
    const rec = journal.recover().find((r) => r.ambiguous);
    assert.ok(rec, 'ambiguous record retained');
    assert.equal(rec.ambiguous.kind, 'reaction');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('ambiguous undecryptable edits are retained, never enqueued', async () => {
  const dir = tmpDir();
  try {
    const { journal, stream, deps } = captureDeps(dir);
    const p = createPipeline(deps);
    p.noteAmbiguous({
      conversationId: '90000180@g.us', conversationType: 'group',
      senderId: '+85290000100', stanzaId: 'S1', targetId: 'T1', reason: 'no_key',
    });
    const rec = journal.recover().find((r) => r.ambiguous);
    assert.ok(rec);
    assert.equal(rec.ambiguous.stanza_id, 'S1');
    assert.equal(stream.length, 0);
    // Denied rooms: nothing retained at all.
    p.noteAmbiguous({ conversationId: 'other@g.us', conversationType: 'group',
      senderId: '+x', stanzaId: 'S2', targetId: null, reason: 'x' });
    assert.equal(journal.recover().filter((r) => r.ambiguous).length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('e2e: denied sender in an allowed room is captured but receipted never', async () => {
  const dir = tmpDir();
  try {
    // Real projection file + real Projection — not a stub.
    writeFileSync(path.join(dir, 'whatsapp.json'), JSON.stringify({
      revision: 1, enabled: true, account_key: 'acct-1',
      rooms: ['90000180@g.us'], dm_allowed_senders: ['+85290000100'],
      room_receipt_senders: { '90000180@g.us': ['+85290000100'] },
      self_aliases: ['+999'], deterministic_triggers: [], receipt_emoji: '👀',
    }));
    const projection = new Projection(dir, 'whatsapp');
    projection._tick(true);
    const { journal, stream, receipts, deps } = captureDeps(dir);
    const p = createPipeline({ ...deps, projection });
    // A stranger IN the allowed room mentions Nora: captured (room admits),
    // but the receipt grant is per-sender — the mention never fires one.
    await p.capture({ ...EVENT, sender_jid: '999000@s.whatsapp.net',
      sender_phone: '+999000', id: '3EB0X1' }, {});
    // The granted sender's identical mention does fire.
    await p.capture({ ...EVENT, id: '3EB0X2' }, {});
    await p.replay();
    assert.equal(stream.length, 2);                          // both captured
    const denied = stream.find((e) => e.message_id === '3EB0X1' || e.message.message_id === '3EB0X1');
    const granted = stream.find((e) => e.message.message_id === '3EB0X2');
    assert.equal(denied.receipt, null);
    assert.equal(granted.receipt.emoji, '👀');
    assert.equal(receipts.length, 1);                        // only the granted one
    assert.equal(receipts[0].request.to_message_id, '3EB0X2');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

// -- metrics ----------------------------------------------------------------------

test('metrics accumulate low-cardinality counters and render text', () => {
  const m = new Metrics();
  m.inc('bridge_capture_total', { platform: 'whatsapp', outcome: 'enqueued' });
  m.inc('bridge_capture_total', { platform: 'whatsapp', outcome: 'enqueued' });
  m.set('bridge_journal_pending', { platform: 'whatsapp' }, 3);
  const text = m.text();
  assert.match(text, /bridge_capture_total\{[^}]*outcome="enqueued"[^}]*\} 2/);
  assert.match(text, /bridge_journal_pending\{[^}]*platform="whatsapp"[^}]*\} 3/);
});

// -- action trace links -------------------------------------------------------------

test('POST /v1/actions links the caller traceparent; invalid context carries none', async () => {
  const { InMemorySpanExporter } = await import('@opentelemetry/sdk-trace-node');
  const { initTracing } = await import('./tracing.js');
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  try {
    const pg = fakePg();
    const store = new ActionStore(pg, { platform: 'whatsapp', accountKey: 'acct-1' });
    pg.works.set('1', workRow(3, { accountKey: 'acct-1' }));
    const ctx = fakeCtx();
    const v1 = createV1({ store, ctx, attachments: null, self: () => null,
      health: () => ({}), metrics: null, tracer });

    const tp = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01';
    const body = { action_id: 'tl-1', owner: { kind: 'work', id: '1', generation: 3 },
      op: 'send', request: REQ };
    const req = fakeReq('POST', '/v1/actions', body);
    req.headers = { traceparent: tp };
    const res = fakeRes();
    await v1(req, res, new URL('http://b/v1/actions'));
    assert.equal(res.json.state, 'confirmed');

    const bad = fakeReq('POST', '/v1/actions', { ...body, action_id: 'tl-2' });
    bad.headers = { traceparent: 'not-a-traceparent' };
    const res2 = fakeRes();
    await v1(bad, res2, new URL('http://b/v1/actions'));
    assert.equal(res2.json.state, 'confirmed');   // invalid context never blocks

    await tracer.flush();
    const spans = exporter.getFinishedSpans().filter((s) => s.name === 'bridge.action.attempt');
    assert.equal(spans.length, 2);
    assert.equal(spans[0].links.length, 1);
    assert.equal(spans[0].links[0].context.traceId, '4bf92f3577b34da6a3ce929d0e0e4736');
    assert.equal(spans[0].links[0].context.spanId, '00f067aa0ba902b7');
    assert.equal(spans[0].attributes['nora.trace_kind'], 'queue');
    assert.equal(spans[1].links.length, 0);
  } finally { await tracer.shutdown().catch(() => {}); }
});
