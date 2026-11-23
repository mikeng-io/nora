// Body bounds on the one operations route: enforced on bytes as they arrive,
// with only `speak` running on past the control bound.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { makeHarness, operationBody, rawStream, GUILD } from './helpers.mjs';

function assertNoEnvelope(res, status, label) {
  assert.equal(res.status, status, label);
  assert.deepEqual(Object.keys(res.body), ['error'], label);
}

test('the control bound is enforced on arriving bytes; only speak runs on to the speech cap', async () => {
  // Every kind shares one route, so the kind is read off the arriving bytes:
  // a body past 64 KiB goes on only if it has already named `speak`.
  const h = await makeHarness();
  const { generation } = await h.join();
  const play = (extra) => ({ operation_id: 'big', kind: 'play',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', ...extra } });

  // A partial upload past the bound is refused at once, not at the deadline.
  const partial = await rawStream(h.port, operationBody(play({ title: 'x'.repeat(70 * 1024) })).slice(0, 70 * 1024),
    { contentLength: 100 * 1024 });
  assertNoEnvelope(partial, 413, 'a partial play upload');
  assert.equal(partial.body.error, 'body_too_large');
  assert.ok(partial.ms < 5000, `refused as it arrived, after ${partial.ms} ms, not at the 15 s deadline`);
  // Whitespace counts: a complete request padded past the bound is refused.
  const padded = operationBody(play({})).replace('{', `{${' '.repeat(70 * 1024)}`);
  assertNoEnvelope(await rawStream(h.port, padded), 413, 'whitespace padding');
  assert.equal(h.procs.length, 0, 'nothing played');
  // A kind repeated after a large body is read as JSON reads it, the last one.
  const repeated = operationBody({ operation_id: 'dup', kind: 'speak',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', pad: 'x'.repeat(70 * 1024) } })
    .replace(/}$/, ',"kind":"play"}');
  assertNoEnvelope(await rawStream(h.port, repeated), 413, 'a later kind decides');

  // speak runs to the speech cap, and no further.
  const speech = await h.op('speak', { guild_id: GUILD, generation,
    audio_b64: Buffer.alloc(2 * 1024 * 1024, 1).toString('base64') });
  assert.equal(speech.outcome, 'complete', 'a 2 MiB speech, 2.7 MiB as base64');
  const overCap = await rawStream(h.port, operationBody({ operation_id: 'huge', kind: 'speak',
    effect: { guild_id: GUILD, generation, audio_b64: 'A'.repeat(4 * 1024 * 1024) } }));
  assertNoEnvelope(overCap, 413, 'over the speech cap');
  // Its ceiling: a speech must name its kind before an effect past the bound.
  const late = await rawStream(h.port, JSON.stringify({ operation_id: 'late',
    effect: { guild_id: GUILD, generation, audio_b64: 'A'.repeat(70 * 1024) }, kind: 'speak' }));
  assertNoEnvelope(late, 413, 'kind after a large effect');

  // A query's bound is a control body's.
  const query = await rawStream(h.port, JSON.stringify({ kind: 'status', params: { guild_id: GUILD }, pad: 'x'.repeat(70 * 1024) }),
    { path: '/v1/voice/queries' });
  assert.equal(query.status, 413);
  await h.close();
});
