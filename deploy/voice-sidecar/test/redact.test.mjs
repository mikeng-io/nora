// Log-redaction tests: signed CDN URLs are credential-ish — query strings
// and userinfo are stripped before anything reaches a log line.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { redactUrl, redactText } from '../src/redact.mjs';
import { createArbiter } from '../src/arbiter.mjs';
import { createFakeClock, createFakePlayer, createFakeProc, createFakeResource, tick } from './helpers.mjs';

test('redactUrl strips query string and userinfo', () => {
  assert.equal(
    redactUrl('https://user:pass@cdn.example.com/path/seg?expire=1&signature=secret'),
    'https://cdn.example.com/path/seg',
  );
  assert.equal(redactUrl('https://cdn.example.com/plain'), 'https://cdn.example.com/plain');
  assert.equal(redactUrl('not a url?x=1'), 'not a url');
  assert.equal(redactUrl(null), null);
});

test('redactText scrubs every URL inside free text and the known secret first', () => {
  const secret = 'https://cdn.example.com/a/b?sig=zzz';
  const out = redactText(`ffmpeg fails on ${secret} and https://other.example.com/x?token=q done`, secret);
  assert.ok(!out.includes('sig=zzz'));
  assert.ok(!out.includes('token=q'));
  assert.ok(out.includes('https://cdn.example.com/a/b'));
});

test('the play log carries the redacted stream_url, never the signed one', async () => {
  const logs = [];
  const clock = createFakeClock();
  const player = createFakePlayer();
  const procs = [];
  const arbiter = createArbiter({
    spawn: (args) => {
      const p = createFakeProc();
      procs.push(p);
      return p;
    },
    createMusicResource: (stream) => createFakeResource(stream),
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    clock,
    getGeneration: () => 'gen1',
    log: (event, fields) => logs.push({ event, ...fields }),
  });
  arbiter.attach(player);
  arbiter.playMusic({ streamUrl: 'https://cdn.example.com/track?expire=9&signature=topsecret' });
  procs[0].data();
  await tick();
  const playLogs = logs.filter((l) => l.event === 'play_requested');
  assert.ok(playLogs.length >= 1);
  const serialized = JSON.stringify(logs);
  assert.ok(!serialized.includes('topsecret'));
  assert.ok(!serialized.includes('expire=9'));
});

test('ffmpeg stderr is captured bounded and URL-scrubbed in the failure log', () => {
  const logs = [];
  const clock = createFakeClock();
  const player = createFakePlayer();
  const proc = createFakeProc();
  proc.stderr = new EventEmitter();
  const arbiter = createArbiter({
    spawn: () => proc,
    createMusicResource: (stream) => createFakeResource(stream),
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    clock,
    getGeneration: () => 'gen1',
    log: (event, fields) => logs.push({ event, ...fields }),
  });
  arbiter.attach(player);
  arbiter.playMusic({ streamUrl: 'https://cdn.example.com/t?sig=abc' });
  proc.data();
  proc.stderr.emit('data', Buffer.from('http error https://cdn.example.com/t?sig=abc 403'));
  proc.close(1);
  const failed = logs.filter((l) => l.event === 'stream-failed');
  assert.equal(failed.length, 1);
  assert.ok(failed[0].stderr.includes('403'));
  assert.ok(!JSON.stringify(logs).includes('sig=abc'));
});

test('play_latency_ms is measured from request arrival, not first ffmpeg bytes', async () => {
  const logs = [];
  const clock = createFakeClock();
  const player = createFakePlayer();
  const procs = [];
  const arbiter = createArbiter({
    spawn: (args) => {
      const p = createFakeProc();
      procs.push(p);
      return p;
    },
    createMusicResource: (stream) => createFakeResource(stream),
    createSpeechResource: (audio, mediaType) => ({ audio, mediaType, playbackDuration: 0 }),
    clock,
    getGeneration: () => 'gen1',
    log: (event, fields) => logs.push({ event, ...fields }),
  });
  arbiter.attach(player);
  arbiter.playMusic({ streamUrl: 'https://cdn.test/a' });
  clock.advance(800); // request-to-ffmpeg-startup time
  procs[0].data();
  clock.advance(50); // first-bytes-to-player time
  await tick();
  const play = logs.filter((l) => l.event === 'play');
  assert.equal(play.length, 1);
  assert.equal(play[0].play_latency_ms, 850); // includes the 800ms before first bytes
});
