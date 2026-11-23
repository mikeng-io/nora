// Body bounds on the one operations route: every body is read up to the
// speech cap; once parsed, only `speak` may exceed the 64 KiB control bound.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { makeHarness, operationBody, rawStream, GUILD } from './helpers.mjs';

function assertNoEnvelope(res, status, label) {
  assert.equal(res.status, status, label);
  assert.deepEqual(Object.keys(res.body), ['error'], label);
}

test('only speak runs past the control bound, whatever the key order or escapes', async () => {
  const h = await makeHarness();
  const { generation } = await h.join();
  const audio = Buffer.alloc(512 * 1024, 1).toString('base64'); // well past 64 KiB as JSON
  const speech = { guild_id: GUILD, generation, audio_b64: audio, media_type: 'audio/mpeg' };

  // A speech whose effect comes before its kind completes.
  const effectFirst = await rawStream(h.port, JSON.stringify({ operation_id: 'effect-first', effect: speech, kind: 'speak' }));
  assert.equal(effectFirst.status, 200);
  assert.equal(effectFirst.body.outcome, 'complete', 'effect before kind');
  // A speech whose kind key is written with a JSON escape completes.
  const escaped = `{"operation_id":"escaped","\\u006bind":"speak","effect":${JSON.stringify(speech)}}`;
  const escapedRes = await rawStream(h.port, escaped);
  assert.equal(escapedRes.body.outcome, 'complete', 'an escaped kind key');

  // A non-speak body over the control bound is 413, whitespace included.
  const play = { operation_id: 'big', kind: 'play',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a' } };
  const padded = `{${' '.repeat(70 * 1024)}${operationBody(play).slice(1)}`;
  assertNoEnvelope(await rawStream(h.port, padded), 413, 'whitespace padding');
  assertNoEnvelope(await rawStream(h.port, operationBody({ ...play,
    effect: { ...play.effect, title: 'x'.repeat(70 * 1024) } })), 413, 'a large play');
  // The bound comes first: an oversized play with no usable effect is 413.
  assertNoEnvelope(await rawStream(h.port, operationBody({ operation_id: 'null-effect', kind: 'play', effect: null,
    pad: 'x'.repeat(70 * 1024) })), 413, 'oversized, effect null');
  // A repeated kind is read as JSON reads it, the last one.
  const repeated = operationBody({ operation_id: 'dup', kind: 'speak',
    effect: { guild_id: GUILD, generation, stream_url: 'https://cdn.test/a', pad: 'x'.repeat(70 * 1024) } });
  assertNoEnvelope(await rawStream(h.port, `${repeated.slice(0, -1)},"kind":"play"}`), 413, 'a later kind decides');
  assert.equal(h.procs.length, 0, 'nothing played');

  // speak runs to the speech cap, and no further.
  const twoMiB = await h.op('speak', { guild_id: GUILD, generation,
    audio_b64: Buffer.alloc(2 * 1024 * 1024, 1).toString('base64') });
  assert.equal(twoMiB.outcome, 'complete', 'a 2 MiB speech, 2.7 MiB as base64');
  const overCap = await rawStream(h.port, operationBody({ operation_id: 'huge', kind: 'speak',
    effect: { guild_id: GUILD, generation, audio_b64: 'A'.repeat(4 * 1024 * 1024) } }));
  assertNoEnvelope(overCap, 413, 'over the speech cap');

  // A query's bound is a control body's, on arrival.
  const query = await rawStream(h.port, JSON.stringify({ kind: 'status', params: { guild_id: GUILD }, pad: 'x'.repeat(70 * 1024) }),
    { path: '/v1/voice/queries' });
  assert.equal(query.status, 413);
  await h.close();
});
