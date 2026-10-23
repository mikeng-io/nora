// The wake-gate wire format, JS half. Its Python twin is
// `deploy/voice-wake/tests/test_codec.py` — one format, two implementations,
// and a change to either is a change to both.
//
// This file could not exist before 2026-09-17: the codec lived in recv.js,
// which imports discord.js at module scope, so every test touching it needed
// a node_modules tree and `pump.test.mjs` had stopped running locally.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { encodeHello, encodeFrame, createFrameParser } from '../src/codec.mjs';

const TOKEN = 'test-bridge-token';

function collect(blob, chunkSize = 0) {
  const frames = [];
  let guild = null;
  const feed = createFrameParser(
    (userId, pcm) => frames.push([userId, [...pcm]]),
    (g) => { guild = g; },
  );
  if (chunkSize === 0) {
    feed(blob);
  } else {
    for (let i = 0; i < blob.length; i += chunkSize) {
      feed(blob.subarray(i, i + chunkSize));
    }
  }
  return { frames, guild };
}

test('a hello names the guild and is not an audio frame', () => {
  const { frames, guild } = collect(
    Buffer.concat([
      encodeHello('100000000000000203', TOKEN),
      encodeFrame('u1', Buffer.from([1, 2])),
      encodeFrame('u2', Buffer.from([3])),
    ]),
  );

  assert.equal(guild, '100000000000000203');
  assert.deepEqual(frames, [['u1', [1, 2]], ['u2', [3]]]);
});

test('the hello survives being split across TCP chunk boundaries', () => {
  // A 19-byte guild id straddles any realistic read boundary, and the parser
  // must hold the partial payload rather than mis-cut the stream.
  const blob = Buffer.concat([
    encodeHello('100000000000000203', TOKEN),
    encodeFrame('u1', Buffer.alloc(3840, 7)),
  ]);

  const { frames, guild } = collect(blob, 1);

  assert.equal(guild, '100000000000000203');
  assert.equal(frames.length, 1);
  assert.equal(frames[0][0], 'u1');
  assert.equal(frames[0][1].length, 3840);
});

test('an empty userId is refused, because 0 is the hello marker', () => {
  // Without this an empty speaker id would encode as a hello and be swallowed
  // as a guild announcement — audio silently vanishing, no error anywhere.
  assert.throws(() => encodeFrame('', Buffer.alloc(0)), /hello marker/);
});

test('a hello with no guild id is refused', () => {
  assert.throws(() => encodeHello(''), /needs a guildId/);
});

test('a hello without the service token is refused, so nothing unauthenticated reaches the gate', () => {
  assert.throws(() => encodeHello('100000000000000203', ''), /needs the service token/);
  assert.throws(() => encodeHello('100000000000000203'), /needs the service token/);
});

test('a hello whose guild id holds a newline is refused, because the newline separates the token', () => {
  assert.throws(() => encodeHello('1\n2', TOKEN), /newline/);
});

test('a hello carries the token after the guild id, behind a newline', () => {
  const frame = encodeHello('g9', 'k');
  assert.equal(frame.toString('hex'), '000000050067390a6b');
  const hellos = [];
  createFrameParser(() => assert.fail('a hello is never an audio frame'), (guild, token) => hellos.push([guild, token]))(frame);
  assert.deepEqual(hellos, [['g9', 'k']]);
});

test('the audio frame layout is BYTE-IDENTICAL to what it was', () => {
  // The hello was added by reusing the existing length byte, NOT by widening
  // the frame. If this changes, every deployed wake gate mis-parses audio.
  assert.equal(
    encodeFrame('u1', Buffer.from([0xab])).toString('hex'),
    '00000004027531ab',
  );
  assert.equal(encodeHello('g9', 'k').toString('hex'), '000000050067390a6b');
});
