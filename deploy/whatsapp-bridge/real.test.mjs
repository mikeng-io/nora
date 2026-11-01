import { randomUUID } from 'node:crypto';
const generatedChallenge0 = randomUUID();
/**
 * real.test.mjs — the focused proofs that need REAL services, not fakes.
 *
 * Redis: a disposable `redis-server` child on a free port proves the atomic
 * Lua contract (source-key-first dedupe, the two-value
 * {stream_id, canonical_event_id} return under key rotation), the
 * NOSCRIPT→EVAL fallback, restart replay of a pending receipt, outage
 * ordering (a later capture cannot overtake an older journaled head), and
 * consumer-group PEL behavior the app reader depends on.
 *
 * Postgres: a throwaway initdb cluster proves the reservation transaction —
 * concurrent same-id POSTs cannot double-execute, immutable context
 * conflicts are conflicts not replays, the in-transaction work-owner fence
 * durably rejects stale owners, and scoped reads/writes + CAS keep one
 * bridge's rows from another's.
 *
 * Each suite skips cleanly when its binaries aren't installed; nothing here
 * touches a live account.
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import net from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { createClient } from 'redis';
import pg from 'pg';

import { ENQUEUE_LUA, ENQUEUE_LUA_SHA1, CONSUMER_GROUP, StreamProducer, streamName, dedupeKey, sourceKey } from './queue.js';
import { Journal } from './journal.js';
import { createPipeline } from './pipeline.js';
import { ActionStore, requestHash } from './store.js';
import { AttachmentStore } from './media.js';

function which(cmd) {
  try {
    return execFileSync('sh', ['-c', `command -v ${cmd}`], { encoding: 'utf8' }).trim() || null;
  } catch { return null; }
}
const REDIS_SERVER = which('redis-server');
const INITDB = which('initdb');
const PG_CTL = which('pg_ctl');
const haveRedis = !!REDIS_SERVER;
const havePg = !!(INITDB && PG_CTL);

const tmp = () => mkdtempSync(path.join(tmpdir(), 'wa-real-'));

async function freePort() {
  const srv = net.createServer();
  await new Promise((res) => srv.listen(0, '127.0.0.1', res));
  const port = srv.address().port;
  await new Promise((res) => srv.close(res));
  return port;
}

// -- disposable redis --------------------------------------------------------

async function startRedis(dir, port) {
  const child = spawn(REDIS_SERVER, [
    '--port', String(port), '--bind', '127.0.0.1',
    '--save', '', '--appendonly', 'no', '--dir', dir,
    '--daemonize', 'no', '--enable-debug-command', 'yes',
  ], { stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error('redis start timeout')), 15000);
    child.stdout.on('data', (d) => {
      if (String(d).includes('Ready to accept connections')) { clearTimeout(to); resolve(); }
    });
    child.on('exit', (c) => { clearTimeout(to); reject(new Error(`redis-server exited ${c}`)); });
  });
  return child;
}

async function redisClient(port, { reconnect = true } = {}) {
  const client = createClient({
    socket: {
      host: '127.0.0.1', port,
      reconnectStrategy: reconnect ? (n) => Math.min(50 + n * 50, 500) : false,
    },
  });
  client.on('error', () => {});                 // reconnect churn is expected here
  await client.connect();
  return client;
}

async function waitClient(client, ms = 10000) {
  const t0 = Date.now();
  for (;;) {
    try { if (await client.ping()) return; } catch { /* still down */ }
    if (Date.now() - t0 > ms) throw new Error('redis client never reconnected');
    await new Promise((r) => setTimeout(r, 60));
  }
}

const STREAM = streamName('whatsapp', 'acct-real');
const envFor = (eventId, srcKey) => JSON.stringify({
  platform: 'whatsapp', account_key: 'acct-real', kind: 'message',
  source_key: srcKey, event_id: eventId,
  conversation_id: '152099@g.us', conversation_type: 'group',
  sender_id: '+85290000100', sent_ms: 1, received_ms: 2, queued_ms: 2,
  time_quality: 'upstream', message: { body: 'x' }, attachments: [],
  trace: null, receipt: null, admission_rev: 1,
});
const mkEnv = (eventId, srcKey) => JSON.parse(envFor(eventId, srcKey));

async function redisHarness() {
  const dir = tmp();
  const port = await freePort();
  const child = await startRedis(dir, port);
  const client = await redisClient(port);
  const producer = new StreamProducer(client, STREAM);
  return {
    dir, port, child, client, producer,
    async bodies() {
      const rows = await client.xRange(STREAM, '-', '+');
      return rows.map((r) => ({ id: r.id, env: JSON.parse(r.message.e) }));
    },
    async stop() {
      try { client.destroy(); } catch { /* already closed */ }
      try { child.kill('SIGKILL'); } catch { /* already dead */ }
      await new Promise((r) => { child.once('exit', r); setTimeout(r, 3000); });
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

test('real redis: source-key-first dedupe + two-value rotation contract', { skip: !haveRedis, timeout: 60000 }, async () => {
  const h = await redisHarness();
  try {
    const e1 = mkEnv('v1:' + 'a'.repeat(64), 'src-1');
    const first = await h.producer.enqueue(e1);
    assert.match(first.streamId, /^\d+-\d+$/);
    assert.equal(first.eventId, e1.event_id);
    assert.equal(await h.client.get(sourceKey(STREAM, 'src-1')), e1.event_id);
    assert.equal(await h.client.get(dedupeKey(e1.event_id)), first.streamId);
    // Same alias again → same pair, no second XADD.
    const again = await h.producer.enqueue(e1);
    assert.deepEqual(again, first);
    // Rotation: same source_key, NEW alias → {prior stream id, ORIGINAL alias}.
    const e2 = mkEnv('v1:' + 'b'.repeat(64), 'src-1');
    const rotated = await h.producer.enqueue(e2);
    assert.equal(rotated.streamId, first.streamId);
    assert.equal(rotated.eventId, e1.event_id);           // canonical = the first alias
    assert.equal(await h.client.get(sourceKey(STREAM, 'src-1')), e1.event_id);
    const rows = await h.bodies();
    assert.equal(rows.length, 1);                          // still ONE stream entry
    assert.equal(rows[0].env.event_id, e1.event_id);
    assert.equal(rows[0].id, first.streamId);
  } finally { await h.stop(); }
});

test('real redis: EVALSHA misses fall back to EVAL (NOSCRIPT only)', { skip: !haveRedis, timeout: 60000 }, async () => {
  const h = await redisHarness();
  try {
    // First call exercises evalSha→NOSCRIPT→eval; the script then sits cached.
    const r1 = await h.producer.enqueue(mkEnv('v1:' + 'c'.repeat(64), 'src-2'));
    assert.equal(r1.eventId, 'v1:' + 'c'.repeat(64));
    // Cached path: evalSha answers directly.
    const r2 = await h.producer.enqueue(mkEnv('v1:' + 'd'.repeat(64), 'src-3'));
    assert.equal(r2.eventId, 'v1:' + 'd'.repeat(64));
    // Flush the cache: evalSha NOSCRIPTs again and still lands via eval.
    await h.client.sendCommand(['SCRIPT', 'FLUSH']);
    const r3 = await h.producer.enqueue(mkEnv('v1:' + 'e'.repeat(64), 'src-4'));
    assert.equal(r3.eventId, 'v1:' + 'e'.repeat(64));
    // A script error that is NOT NOSCRIPT must surface, not fall back.
    await assert.rejects(
      h.client.evalSha(ENQUEUE_LUA_SHA1.replace(/^./, ENQUEUE_LUA_SHA1[0] === '0' ? '1' : '0'), { keys: [], arguments: [] }),
      /NOSCRIPT/i,
    );
    assert.equal((await h.bodies()).length, 3);
  } finally { await h.stop(); }
});

// -- pipeline over the disposable redis ---------------------------------------

const EVENT = {
  type: 'message', id: '3EB0F1', chat_jid: '152099@g.us', is_group: true,
  from_me: false, sender_jid: '85290000100@s.whatsapp.net',
  sender_phone: '+85290000100', sender_lid: null, push_name: null,
  text: 'hello', timestamp_ms: 1730300000000,
  mentioned: [], quoted: null, media: [], edit_of: null,
};

function pipelineDeps(dir, producer, receipts) {
  const journal = new Journal(path.join(dir, 'journal.log'));
  return {
    journal,
    deps: {
      platform: 'whatsapp', accountKey: 'acct-real',
      correlationKey: Buffer.alloc(32, 0xa1), keyVersion: 1,
      journal, producer,
      projection: { revision: 1, admitted: () => true, addressed: () => true, receiptEmoji: '👀' },
      attachments: new AttachmentStore(path.join(dir, 'att')),
      runReceipt: async ({ event_id, request }) => {
        receipts.push({ event_id, request });
        return { state: 'confirmed' };
      },
      metrics: null, jlog: () => {},
    },
  };
}

test('real redis: restart replay — pending receipt completes, nothing re-enqueues', { skip: !haveRedis, timeout: 60000 }, async () => {
  const h = await redisHarness();
  const dir = tmp();
  const receipts = [];
  try {
    const { journal, deps } = pipelineDeps(dir, h.producer, receipts);
    // Crash window: the receipt attempt throws AFTER the enqueue is durable.
    let crash = true;
    deps.runReceipt = async ({ event_id, request }) => {
      if (crash) throw new Error('simulated crash after enqueue');
      receipts.push({ event_id, request });
      return { state: 'confirmed' };
    };
    const p1 = createPipeline(deps);
    await p1.capture(EVENT, {});
    await p1.replay();
    assert.equal((await h.bodies()).length, 1);
    assert.equal(journal.plan(journal.recover()).pendingReceipts, 1);
    // "Restart": a fresh pipeline over the same journal completes the phase.
    crash = false;
    const p2 = createPipeline({ ...deps });
    await p2.replay();
    assert.equal((await h.bodies()).length, 1);          // no second XADD
    assert.equal(receipts.length, 1);                     // receipt ran once
    assert.equal(receipts[0].request.to_message_id, '3EB0F1');
    assert.equal(receipts[0].request.emoji, '👀');
    const plan = journal.plan(journal.recover());
    assert.equal(plan.pendingEnqueues + plan.pendingReceipts, 0);
    await p2.replay();
    assert.equal(receipts.length, 1);                     // settled stays settled
  } finally { await h.stop(); rmSync(dir, { recursive: true, force: true }); }
});

test('real redis: outage — a later capture cannot overtake the journaled head', { skip: !haveRedis, timeout: 90000 }, async () => {
  const dir0 = tmp();
  const port = await freePort();
  const child = await startRedis(dir0, port);
  let client = await redisClient(port, { reconnect: false });
  const producer = new StreamProducer(client, STREAM);
  const dir = tmp();
  const receipts = [];
  try {
    const { journal, deps } = pipelineDeps(dir, producer, receipts);
    const p = createPipeline(deps);
    // Redis dies hard. With no reconnect the dead client's queued commands
    // reject promptly — the journal holds the events either way.
    child.kill('SIGKILL');
    await new Promise((r) => setTimeout(r, 250));
    try { client.destroy(); } catch { /* half-dead already */ }
    // Both captures journal durably while the server is down — capture() must
    // NOT wait on Redis.
    await p.capture({ ...EVENT, id: '3EB0F2', text: 'first' }, {});
    await p.capture({ ...EVENT, id: '3EB0F3', text: 'second' }, {});
    assert.equal(journal.plan(journal.recover()).pendingEnqueues, 2);
    // Server returns on the same port; the producer is re-pointed at a fresh
    // connection (bridge.js recreates its client the same way on reconnect).
    const child2 = await startRedis(dir0, port);
    try {
      client = await redisClient(port);
      producer.client = client;
      await waitClient(client);
      await p.replay();
      const rows = await client.xRange(STREAM, '-', '+');
      // Head-first: journal order is publish order.
      assert.deepEqual(rows.map((r) => JSON.parse(r.message.e).message.body), ['first', 'second']);
      assert.deepEqual(receipts.map((r) => r.request.to_message_id), ['3EB0F2', '3EB0F3']);
    } finally {
      try { client.destroy(); } catch { /* closed */ }
      child2.kill('SIGKILL');
      await new Promise((r) => { child2.once('exit', r); setTimeout(r, 3000); });
    }
  } finally { rmSync(dir, { recursive: true, force: true }); rmSync(dir0, { recursive: true, force: true }); }
});

test('real redis: consumer group PEL — pending until ACK', { skip: !haveRedis, timeout: 60000 }, async () => {
  const h = await redisHarness();
  try {
    await h.producer.enqueue(mkEnv('v1:' + 'f'.repeat(64), 'src-9'));
    await h.producer.enqueue(mkEnv('v1:' + '0'.repeat(64), 'src-10'));
    await h.client.xGroupCreate(STREAM, CONSUMER_GROUP, '0', { MKSTREAM: true });
    const read = await h.client.xReadGroup(CONSUMER_GROUP, 'probe-1', [{ key: STREAM, id: '>' }], { COUNT: 10 });
    const msgs = read?.[0]?.messages ?? [];
    assert.equal(msgs.length, 2);
    assert.equal(typeof msgs[0].message.e, 'string');      // the wire's one field
    const pend = await h.client.xPending(STREAM, CONSUMER_GROUP);
    assert.equal(pend.pending, 2);
    await h.client.xAck(STREAM, CONSUMER_GROUP, msgs[0].id);
    assert.equal((await h.client.xPending(STREAM, CONSUMER_GROUP)).pending, 1);
  } finally { await h.stop(); }
});

// -- disposable postgres --------------------------------------------------------

const PG_DDL = `
  CREATE TABLE messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform text NOT NULL,
    conversation_id text NOT NULL,
    is_nora integer NOT NULL DEFAULT 0,
    delivery_status text NOT NULL DEFAULT 'pending',
    raw_json text
  );
  CREATE TABLE otp_challenge (
    challenge_id uuid PRIMARY KEY,
    person_id text NOT NULL,
    purpose text NOT NULL,
    platform text NOT NULL,
    code_hash text NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
  );
  CREATE TABLE bridge_event (
    id text PRIMARY KEY,
    platform text NOT NULL,
    account_key text NOT NULL,
    conversation_id text NOT NULL,
    source_digest text NOT NULL,
    state text NOT NULL DEFAULT 'open'
  );
  CREATE TABLE bridge_event_alias (
    event_id text PRIMARY KEY,
    event_ref text NOT NULL REFERENCES bridge_event(id)
  );
  CREATE TABLE bridge_work (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    event_id text REFERENCES bridge_event(id),
    kind text NOT NULL,
    ordinal integer NOT NULL,
    platform text NOT NULL,
    account_key text NOT NULL,
    conversation_id text NOT NULL,
    due_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL DEFAULT 'pending',
    generation integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    payload jsonb NOT NULL,
    result jsonb,
    lease_owner text,
    leased_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE (event_id, kind, ordinal),
    CHECK (state IN ('pending','running','done','intentional_skip',
                     'retryable_failure','failed_retained','uncertain','cancelled'))
  );
  CREATE TABLE bridge_action (
    action_id text PRIMARY KEY,
    platform text NOT NULL,
    account_key text NOT NULL,
    owner_kind text NOT NULL,
    owner_id text NOT NULL,
    owner_generation integer,
    op text NOT NULL,
    request jsonb NOT NULL,
    request_hash text NOT NULL,
    state text NOT NULL DEFAULT 'prepared',
    platform_ref text,
    platform_ms bigint,
    error jsonb,
    message_id bigint REFERENCES messages(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    completed_at timestamptz,
    CHECK (state IN ('prepared','started','confirmed','rejected','uncertain')),
    CHECK (owner_kind IN ('work','event_receipt','otp_challenge','live_invocation'))
  );
`;

async function pgHarness() {
  const dir = tmp();
  const data = path.join(dir, 'data');
  const port = await freePort();
  execFileSync(INITDB, ['-D', data, '-A', 'trust', '-U', 'postgres', '--no-sync'], { stdio: 'ignore' });
  execFileSync(PG_CTL, [
    '-D', data, '-w', '-t', '30', '-l', path.join(dir, 'pg.log'),
    '-o', `-p ${port} -F -c listen_addresses=127.0.0.1 -c unix_socket_directories=${dir}`,
    'start',
  ], { stdio: 'ignore' });
  const pool = new pg.Pool({ host: '127.0.0.1', port, user: 'postgres', database: 'postgres', max: 8 });
  await pool.query(PG_DDL);
  const store = new ActionStore(pool, { platform: 'whatsapp', accountKey: 'acct-real' });
  const other = new ActionStore(pool, { platform: 'whatsapp', accountKey: 'acct-other' });
  return {
    dir, pool, store, other,
    async stop() {
      try { await pool.end(); } catch { /* down already */ }
      try { execFileSync(PG_CTL, ['-D', data, '-m', 'immediate', 'stop'], { stdio: 'ignore' }); } catch { /* best effort */ }
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

const OWNER_WORK = { kind: 'work', id: 'w-7', generation: 3 };
const REQ = { target: { kind: 'dm', id: 'conv-1' }, text: 'hi' };

test('real pg: concurrent reserve of one action_id executes exactly once', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    await h.pool.query(
      `INSERT INTO bridge_work (id, kind, ordinal, platform, account_key, conversation_id, state, generation, payload, lease_owner, leased_until)
       VALUES (7, 'send', 0, 'whatsapp', 'acct-real', 'conv-1', 'running', 3, '{}', 'w-test', now() + interval '5 minutes')`);
    const args = { actionId: 'w-7:send:0', owner: OWNER_WORK, op: 'send', request: REQ };
    // Two truly concurrent reservations on separate pool connections — the
    // row lock serializes them; only one can win 'execute'.
    const [a, b] = await Promise.all([h.store.reserve(args), h.store.reserve(args)]);
    const verdicts = [a.verdict, b.verdict].sort();
    assert.deepEqual(verdicts, ['execute', 'replay']);
    const replay = a.verdict === 'replay' ? a : b;
    assert.equal(replay.row.state, 'started');            // the winner's mark
    // A third caller never re-executes the in-flight action.
    const c = await h.store.reserve(args);
    assert.equal(c.verdict, 'replay');
    assert.equal(c.row.state, 'started');
  } finally { await h.stop(); }
});

test('real pg: immutable context conflicts and no-blind-retry replay', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    await h.pool.query(
      `INSERT INTO bridge_work (id, kind, ordinal, platform, account_key, conversation_id, state, generation, payload, lease_owner, leased_until)
       VALUES (7, 'send', 0, 'whatsapp', 'acct-real', 'conv-1', 'running', 3, '{}', 'w-test', now() + interval '5 minutes')`);
    const base = { actionId: 'w-7:send:0', owner: OWNER_WORK, op: 'send', request: REQ };
    const r1 = await h.store.reserve(base);
    assert.equal(r1.verdict, 'execute');
    // Same id, different op → conflict (not a replay of the started row).
    const c1 = await h.store.reserve({ ...base, op: 'react' });
    assert.equal(c1.verdict, 'conflict');
    // Same id, different owner generation → conflict.
    const c2 = await h.store.reserve({ ...base, owner: { ...OWNER_WORK, generation: 4 } });
    assert.equal(c2.verdict, 'conflict');
    // Same id, different request body → conflict.
    const c3 = await h.store.reserve({ ...base, request: { ...REQ, text: 'other' } });
    assert.equal(c3.verdict, 'conflict');
    // Same id + identical context → replay, never a second execute.
    const c4 = await h.store.reserve(base);
    assert.equal(c4.verdict, 'replay');
    assert.equal(c4.row.state, 'started');
    // finish + re-reserve returns the recorded verdict.
    assert.equal(await h.store.finish('w-7:send:0', 'confirmed', { platformRef: '3EB0', platformMs: 99 }), 1);
    const c5 = await h.store.reserve(base);
    assert.equal(c5.verdict, 'replay');
    assert.equal(c5.row.state, 'confirmed');
    assert.equal(c5.row.platform_ref, '3EB0');
  } finally { await h.stop(); }
});

test('real pg: work-owner fence — stale/unknown/wrong-target owners rejected durably', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    await h.pool.query(
      `INSERT INTO bridge_work (id, kind, ordinal, platform, account_key, conversation_id, state, generation, payload, lease_owner, leased_until)
       VALUES (7, 'send', 0, 'whatsapp', 'acct-real', 'conv-1', 'running', 3, '{}', 'w-test', now() + interval '5 minutes')`);
    // Current generation ≠ claimed → fenced.
    const stale = await h.store.reserve({
      actionId: 'w-7:send:1', owner: { ...OWNER_WORK, generation: 2 }, op: 'send', request: REQ });
    assert.equal(stale.verdict, 'replay');
    assert.equal(stale.row.state, 'rejected');
    assert.equal(stale.row.error.kind, 'fenced');
    // Sanity mid-sequence: a current running owner still executes.
    const live = await h.store.reserve({
      actionId: 'w-7:send:2', owner: OWNER_WORK, op: 'send', request: REQ });
    assert.equal(live.verdict, 'execute');
    // Work row finished → fenced.
    await h.pool.query(`UPDATE bridge_work SET state='done' WHERE id=7`);
    const notRunning = await h.store.reserve({
      actionId: 'w-7:send:3', owner: OWNER_WORK, op: 'send', request: REQ });
    assert.equal(notRunning.row.state, 'rejected');
    // Unknown work id → fenced, never guessed.
    const ghost = await h.store.reserve({
      actionId: 'w-99:send:0', owner: { kind: 'work', id: 'w-99', generation: 0 }, op: 'send', request: REQ });
    assert.equal(ghost.row.state, 'rejected');
    // Different target conversation than the work row's → fenced.
    await h.pool.query(`UPDATE bridge_work SET state='running' WHERE id=7`);
    const wrongConv = await h.store.reserve({
      actionId: 'w-7:send:4', owner: OWNER_WORK, op: 'send',
      request: { ...REQ, target: { kind: 'dm', id: 'conv-OTHER' } } });
    assert.equal(wrongConv.row.state, 'rejected');
    // A fenced verdict replays forever — never re-executes.
    const again = await h.store.reserve({
      actionId: 'w-7:send:1', owner: { ...OWNER_WORK, generation: 2 }, op: 'send', request: REQ });
    assert.equal(again.verdict, 'replay');
    assert.equal(again.row.state, 'rejected');
  } finally { await h.stop(); }
});

test('real pg: scoped get/finish + CAS — one bridge cannot see or overwrite another', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    await h.pool.query(
      `INSERT INTO bridge_work (id, kind, ordinal, platform, account_key, conversation_id, state, generation, payload, lease_owner, leased_until)
       VALUES (7, 'send', 0, 'whatsapp', 'acct-real', 'conv-1', 'running', 3, '{}', 'w-test', now() + interval '5 minutes')`);
    const args = { actionId: 'w-7:send:0', owner: OWNER_WORK, op: 'send', request: REQ };
    await h.store.reserve(args);
    // The OTHER account's store sees nothing and writes nothing.
    assert.equal(await h.other.get('w-7:send:0'), null);
    assert.equal(await h.other.finish('w-7:send:0', 'confirmed', { platformRef: 'x' }), 0);
    // Its reserve of the same id is a conflict, not a takeover.
    const foreign = await h.other.reserve(args);
    assert.equal(foreign.verdict, 'conflict');
    // CAS: a late finish cannot overwrite the terminal verdict.
    assert.equal(await h.store.finish('w-7:send:0', 'confirmed', { platformRef: '3EB0', platformMs: 5 }), 1);
    assert.equal(await h.store.finish('w-7:send:0', 'rejected', { error: { kind: 'late' } }), 0);
    assert.equal(await h.store.finish('w-7:send:0', 'started', {}), 0);
    const row = await h.store.get('w-7:send:0');
    assert.equal(row.state, 'confirmed');
    assert.equal(row.platform_ref, '3EB0');
    assert.ok(row.completed_at);
  } finally { await h.stop(); }
});

test('real pg: requestHash is canonical-JSON stable', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    // Key order must not matter — the app hashes the same canonical shape.
    const a = requestHash({ target: { kind: 'dm', id: 'c' }, text: 'x', emoji: '👀' });
    const b = requestHash({ emoji: '👀', text: 'x', target: { id: 'c', kind: 'dm' } });
    assert.equal(a, b);
    assert.match(a, /^[0-9a-f]{64}$/);
  } finally { await h.stop(); }
});

test('real pg: non-work owner parity — otp/live/receipt/lease against real rows', { skip: !havePg, timeout: 90000 }, async () => {
  const h = await pgHarness();
  try {
    const CID = generatedChallenge0;
    await h.pool.query(
      `INSERT INTO otp_challenge (challenge_id, person_id, purpose, platform, code_hash, issued_at, expires_at)
       VALUES ($1, 'person-1', 'sign_in', 'whatsapp', '00', now(), now() + interval '10 minutes')`, [CID]);
    await h.pool.query(
      `INSERT INTO messages (platform, conversation_id, is_nora, delivery_status, raw_json)
       VALUES ('whatsapp', 'conv-1', 1, 'pending', $1)`, [JSON.stringify({ tag_id: `otp-${CID}` })]);

    // otp_challenge → execute, message_id persisted, request rests redacted.
    const otp = await h.store.reserve({ actionId: 'otp-r1',
      owner: { kind: 'otp_challenge', id: CID, generation: null }, op: 'send',
      request: { ...REQ, text: 'code 123456' } });
    assert.equal(otp.verdict, 'execute');
    const otpRow = await h.store.get('otp-r1');
    assert.ok(Number(otpRow.message_id) > 0);  // int8 arrives as a string
    assert.equal(otpRow.request.text, '[redacted]');
    // The full request still fences: a second code on the same id conflicts.
    const clash = await h.store.reserve({ actionId: 'otp-r1',
      owner: { kind: 'otp_challenge', id: CID, generation: null }, op: 'send',
      request: { ...REQ, text: 'code 999999' } });
    assert.equal(clash.verdict, 'conflict');
    // Expired challenge → fenced.
    await h.pool.query(`UPDATE otp_challenge SET expires_at = now() - interval '1 minute' WHERE challenge_id = $1`, [CID]);
    const dead = await h.store.reserve({ actionId: 'otp-r2',
      owner: { kind: 'otp_challenge', id: CID, generation: null }, op: 'send', request: REQ });
    assert.equal(dead.row.state, 'rejected');

    // live_invocation → the named pending row, scoped by platform+conversation.
    const li = await h.store.reserve({ actionId: 'li-r1',
      owner: { kind: 'live_invocation', id: String(otpRow.message_id), generation: null },
      op: 'send', request: REQ });
    assert.equal(li.verdict, 'execute');
    // A foreign-platform row is not an owner.
    await h.pool.query(
      `INSERT INTO messages (platform, conversation_id, is_nora, delivery_status, raw_json)
       VALUES ('signal', 'conv-1', 1, 'pending', '{}')`);
    const foreign = await h.pool.query(`SELECT max(id) AS id FROM messages`);
    const alien = await h.store.reserve({ actionId: 'li-r2',
      owner: { kind: 'live_invocation', id: String(foreign.rows[0].id), generation: null },
      op: 'send', request: REQ });
    assert.equal(alien.row.state, 'rejected');

    // event_receipt → journal intents injected; terminal event revokes.
    const recStore = new ActionStore(h.pool, { platform: 'whatsapp', accountKey: 'acct-real',
      receiptIntents: () => new Set(['v1:zzz']) });
    const minted = await recStore.reserve({ actionId: 'er-r1',
      owner: { kind: 'event_receipt', id: 'v1:ghost', generation: null }, op: 'react', request: REQ });
    assert.equal(minted.row.error.reason, 'no_journaled_receipt_intent');
    const ok = await recStore.reserve({ actionId: 'er-r2',
      owner: { kind: 'event_receipt', id: 'v1:zzz', generation: null }, op: 'react', request: REQ });
    assert.equal(ok.verdict, 'execute');
    await h.pool.query(
      `INSERT INTO bridge_event (id, platform, account_key, conversation_id, source_digest, state)
       VALUES ('e-real', 'whatsapp', 'acct-real', 'conv-1', 'v1:zzz', 'done')`);
    const revoked = await recStore.reserve({ actionId: 'er-r3',
      owner: { kind: 'event_receipt', id: 'v1:zzz', generation: null }, op: 'react', request: REQ });
    assert.equal(revoked.row.error.reason, 'receipt_event_terminal');

    // Lease-dead running work → fenced on the DB clock, sweep or no sweep.
    await h.pool.query(
      `INSERT INTO bridge_work (id, kind, ordinal, platform, account_key, conversation_id, state, generation, payload, lease_owner, leased_until)
       VALUES (8, 'send', 0, 'whatsapp', 'acct-real', 'conv-1', 'running', 3, '{}', 'w-dead', now() - interval '1 minute')`);
    const lapsed = await h.store.reserve({ actionId: 'w-8:send:0',
      owner: { kind: 'work', id: 'w-8', generation: 3 }, op: 'send', request: REQ });
    assert.equal(lapsed.row.state, 'rejected');
    assert.equal(lapsed.row.error.reason, 'lease_expired');
  } finally { await h.stop(); }
});
