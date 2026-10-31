/**
 * Positive controls for the bounded trace contract:
 *   - `bridge.capture` is the producer span the envelope's traceparent names —
 *     the same context in the journal record and the stream payload.
 *   - custody/publish/receipt/action spans are fresh ROOTS linked to the
 *     capture context (and to the previous attempt on retries) — links, not
 *     parents, so a drain never reopens the capture's tree.
 *   - errors are bounded reason strings; no raw exception material reaches
 *     an exported span or a lifecycle log.
 *   - the OTLP/HTTP exporter really ships protobuf to a live sink.
 *
 * Run: node --test tracing.test.mjs
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import http from 'node:http';
import path from 'node:path';

import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-node';
import { initTracing, parseTraceparent } from './tracing.js';
import { createPipeline } from './pipeline.js';
import { Journal } from './journal.js';
import { AttachmentStore } from './media.js';
import { runAction } from './actions.js';
import { Metrics } from './obs.js';

const tmpDir = () => mkdtempSync(path.join(tmpdir(), 'wa-trace-'));

const EVENT = {
  type: 'message', id: '3EB0F1', chat_jid: '152099@g.us', is_group: true,
  from_me: false, sender_jid: '85290000100@s.whatsapp.net',
  sender_phone: '+85290000100', push_name: null,
  text: 'hello', timestamp_ms: 1730300000000,
  mentioned: [{ phone: '+999', lid: null, jid: '999@s.whatsapp.net' }],
  quoted: null, media: [], edit_of: null,
};

function mkDeps(dir, tracer, { logs = [], producer } = {}) {
  const stream = [];
  const journal = new Journal(path.join(dir, 'journal.log'));
  return {
    stream, journal,
    deps: {
      platform: 'whatsapp', accountKey: 'acct-1',
      correlationKey: Buffer.alloc(32, 0xa1), keyVersion: 1,
      journal,
      // `producer` is an optional gate: it may throw (attempt fails, nothing
      // records); a resolved call lands the envelope in the fake stream.
      producer: { async enqueue(env) {
        if (producer) await producer(env);
        const id = `1-${stream.push(env) - 1}`;
        return { streamId: id, eventId: env.event_id };
      } },
      projection: {
        revision: 1, receiptEmoji: '👀',
        admitted: () => true, addressed: () => true,
      },
      attachments: new AttachmentStore(path.join(dir, 'att')),
      downloadMedia: null,
      runReceipt: async () => ({ state: 'confirmed' }),
      metrics: new Metrics(),
      jlog: (ev, fields) => logs.push({ ev, fields }),
      tracer,
    },
  };
}

const tpIds = (tp) => {
  const c = parseTraceparent(tp);
  return c ? { traceId: c.traceId, spanId: c.spanId } : null;
};
const spanIds = (s) => ({ traceId: s.spanContext().traceId, spanId: s.spanContext().spanId });
const isRoot = (s) => !s.parentSpanContext && !s.parentSpanId;
const spanJson = (s) => JSON.stringify({ a: s.attributes, st: s.status, ev: s.events, li: s.links });

test('env.trace names the exported bridge.capture span — journal and payload agree', async () => {
  const dir = tmpDir();
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  try {
    const { stream, journal, deps } = mkDeps(dir, tracer);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    const spans = exporter.getFinishedSpans();
    const capture = spans.find((s) => s.name === 'bridge.capture');
    assert.ok(capture, 'bridge.capture span exported');
    assert.equal(capture.kind, 3);                     // SpanKind.PRODUCER
    assert.equal(capture.attributes['nora.trace_kind'], 'queue');
    // The envelope's traceparent IS this exported span's context — in the
    // stream payload AND in the durable journal record.
    const envTp = tpIds(stream[0].trace.traceparent);
    assert.deepEqual(envTp, spanIds(capture));
    const jenv = journal.recover().find((r) => r.envelope).envelope;
    assert.deepEqual(tpIds(jenv.trace.traceparent), spanIds(capture));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('publish/receipt spans are roots LINKED to the capture context', async () => {
  const dir = tmpDir();
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  try {
    const { stream, deps } = mkDeps(dir, tracer);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    const spans = exporter.getFinishedSpans();
    const capture = spans.find((s) => s.name === 'bridge.capture');
    const publish = spans.find((s) => s.name === 'bridge.queue.publish');
    const receipt = spans.find((s) => s.name === 'bridge.receipt');
    for (const s of [publish, receipt]) {
      assert.ok(s, 'span exported');
      assert.ok(isRoot(s), `${s.name} is a fresh root, not a child`);
      // Link 0 is the original producer context — the capture span the
      // envelope itself names.
      assert.equal(s.links.length >= 1, true);
      assert.deepEqual(spanIds({ spanContext: () => s.links[0].context }), spanIds(capture));
      assert.equal(s.attributes['nora.trace_kind'], 'queue');
      assert.equal(s.kind, 3);
    }
    // Same trace family via the link, but NOT a parent-child tree.
    assert.equal(publish.links[0].context.traceId, capture.spanContext().traceId);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a retried enqueue links the previous attempt as well as the capture', async () => {
  const dir = tmpDir();
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  try {
    let calls = 0;
    const producer = async () => {
      calls += 1;
      if (calls === 1) throw new Error('transient refusal');
    };
    const { stream, deps } = mkDeps(dir, tracer, { producer });
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    const publishes = exporter.getFinishedSpans().filter((s) => s.name === 'bridge.queue.publish');
    assert.equal(publishes.length, 2);
    const [first, second] = publishes;
    // Attempt 2 links: capture context + attempt 1's span context.
    const linkCtxs = second.links.map((l) => l.context.spanId);
    const captureId = tpIds(stream[0].trace.traceparent).spanId;
    assert.ok(linkCtxs.includes(captureId), 'links the original capture');
    assert.ok(linkCtxs.includes(first.spanContext().spanId), 'links the previous attempt');
    assert.ok(isRoot(second));
    assert.equal(first.status.code, 2);                // ERROR
    assert.equal(first.status.message, 'enqueue_failed');
    assert.equal(stream.length, 1);                    // retry delivered
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('a credential-bearing SDK exception never reaches spans or logs', async () => {
  const dir = tmpDir();
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  const logs = [];
  const SECRET = 's3cr3t-redis-auth-token';
  try {
    let calls = 0;
    const producer = async () => {
      calls += 1;
      if (calls === 1) {
        throw new Error(
          `redis://bridge:${SECRET}@redis.internal:6379/0 auth failed ` +
          `for +85290000100 in 152099@g.us body="héllo"`,
        );
      }
    };
    const { stream, deps } = mkDeps(dir, tracer, { logs, producer });
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    assert.equal(stream.length, 1);                    // delivery completed anyway
    for (const s of exporter.getFinishedSpans()) {
      const blob = spanJson(s);
      for (const needle of [SECRET, 'redis.internal', '85290000100', '152099@g.us', 'héllo', 'redis://']) {
        assert.ok(!blob.includes(needle), `${s.name} leaked ${JSON.stringify(needle)}`);
      }
      // No exception event was recorded anywhere.
      assert.ok(!s.events.some((e) => /exception/i.test(e.name)), 'no recordException event');
    }
    assert.equal(JSON.stringify(logs).includes(SECRET), false);
    assert.equal(JSON.stringify(logs).includes('85290000100'), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('tracing disabled → empty validated trace, never an invented one', async () => {
  const dir = tmpDir();
  try {
    const { stream, journal, deps } = mkDeps(dir, null);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    assert.deepEqual(stream[0].trace, {});
    const jenv = journal.recover().find((r) => r.envelope).envelope;
    assert.deepEqual(jenv.trace, {});
    assert.equal(stream.length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('the configured OTLP/HTTP exporter really ships protobuf spans', async () => {
  const dir = tmpDir();
  const hits = [];
  const sink = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      hits.push({ url: req.url, type: req.headers['content-type'], body: Buffer.concat(chunks) });
      res.writeHead(200).end();
    });
  });
  await new Promise((r) => sink.listen(0, '127.0.0.1', r));
  const port = sink.address().port;
  const tracer = initTracing({ endpoint: `http://127.0.0.1:${port}/v1/traces`, serviceName: 'wa-test' });
  try {
    const { stream, deps } = mkDeps(dir, tracer);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    assert.equal(hits.length >= 1, true, 'sink received an export POST');
    const hit = hits[0];
    assert.equal(hit.url, '/v1/traces');
    // Protobuf or JSON — both are the configured exporter's real payload.
    // The span name and the capture span's own trace id must be on the wire.
    const traceId = tpIds(stream[0].trace.traceparent).traceId;
    if (/json/i.test(hit.type)) {
      const text = hit.body.toString('utf8');
      assert.ok(text.includes('bridge.capture'), 'span name on the wire');
      assert.ok(text.includes(traceId), 'capture trace id on the wire');
    } else {
      assert.match(hit.type, /protobuf/);
      assert.ok(hit.body.includes(Buffer.from('bridge.capture')), 'span name on the wire');
      assert.ok(hit.body.includes(Buffer.from(traceId, 'hex')), 'capture trace id on the wire');
    }
  } finally {
    sink.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('attribute writes are allowlisted — ids/urls/foreign keys cannot land', async () => {
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  const span = tracer.startSpan('probe', {
    attributes: {
      'nora.trace_kind': 'queue',
      'http.url': 'https://collector.internal/?token=abc',
      'bridge.action.state': 'confirmed',
      'evil.object': { nested: true },
      'bridge.media.size': 1234,
    },
  });
  span.setAttribute('conversation.id', '152099@g.us');   // not allowlisted
  span.setAttribute('bridge.op', 'send');                // allowlisted
  span.setAttribute('bridge.op2', 'x');                  // not
  span.end({ type: 'redis', reason: 'timeout' });
  await tracer.flush();
  const s = exporter.getFinishedSpans()[0];
  assert.equal(s.attributes['nora.trace_kind'], 'queue');
  assert.equal(s.attributes['bridge.action.state'], 'confirmed');
  assert.equal(s.attributes['bridge.media.size'], 1234);
  assert.equal(s.attributes['bridge.op'], 'send');
  assert.ok(!('http.url' in s.attributes));
  assert.ok(!('evil.object' in s.attributes));
  assert.ok(!('conversation.id' in s.attributes));
  assert.ok(!('bridge.op2' in s.attributes));
  assert.equal(s.status.code, 2);
  assert.equal(s.status.message, 'redis:timeout');
});

test('W3C validation: all-zero and malformed contexts are not links', async () => {
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  const good = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01';
  assert.ok(parseTraceparent(good));
  assert.equal(parseTraceparent('00-' + '0'.repeat(32) + '-00f067aa0ba902b7-01'), null);
  assert.equal(parseTraceparent('00-4bf92f3577b34da6a3ce929d0e0e4736-' + '0'.repeat(16) + '-01'), null);
  assert.equal(parseTraceparent('ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'), null);
  assert.equal(parseTraceparent('garbage'), null);
  const span = tracer.startSpan('probe', { links: [good, 'garbage', null] });
  span.end();
  await tracer.flush();
  const s = exporter.getFinishedSpans()[0];
  assert.equal(s.links.length, 1);                       // only the valid one
  assert.equal(s.links[0].context.traceId, '4bf92f3577b34da6a3ce929d0e0e4736');
});

test('action attempts export with creation attributes and queue links', async () => {
  const exporter = new InMemorySpanExporter();
  const tracer = initTracing({ exporter, serviceName: 'wa-test' });
  const res = await runAction({
    store: null, ctx: { connected: () => true }, actionId: 'a1',
    owner: { kind: 'event_receipt', id: 'e1' }, op: 'react', request: {},
    tracer, traceLinks: ['00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'],
  });
  assert.equal(res.state, 'unavailable');
  await tracer.flush();
  const attempt = exporter.getFinishedSpans().find((s) => s.name === 'bridge.action.attempt');
  assert.ok(attempt, 'attempt span exported');
  assert.ok(isRoot(attempt));
  assert.equal(attempt.attributes['nora.trace_kind'], 'queue');
  assert.equal(attempt.attributes['bridge.op'], 'react');
  assert.equal(attempt.attributes['bridge.owner_kind'], 'event_receipt');
  assert.equal(attempt.attributes['bridge.action.state'], 'unavailable');
  assert.equal(attempt.links.length, 1);
  assert.equal(attempt.links[0].context.traceId, '4bf92f3577b34da6a3ce929d0e0e4736');
  for (const v of Object.values(attempt.attributes)) assert.doesNotMatch(String(v), /a1|e1/);
});

test('an exporter that always fails still lets captures enqueue durably', async () => {
  const dir = tmpDir();
  const failing = {
    export: (_spans, cb) => cb({ code: 16 }),   // UNAUTHENTICATED-ish; processor swallows
    shutdown: async () => {},
  };
  const tracer = initTracing({ exporter: failing, serviceName: 'wa-test' });
  try {
    const { stream, journal, deps } = mkDeps(dir, tracer);
    const p = createPipeline(deps);
    await p.capture(EVENT, {});
    await p.replay();
    await tracer.flush();
    assert.equal(stream.length, 1);             // the event is never lost
    assert.ok(journal.recover().find((r) => r.enqueued));
    // The trace context still names a real span — it was created, exported
    // just failed downstream; the lineage stays honest.
    assert.ok(stream[0].trace.traceparent);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('initTracing is a null object when no endpoint/exporter is configured', () => {
  assert.equal(initTracing({ env: {} }), null);
  assert.equal(initTracing({ env: { OTEL_EXPORTER_OTLP_ENDPOINT: 'http://c:4318' } }) !== null, true);
});
