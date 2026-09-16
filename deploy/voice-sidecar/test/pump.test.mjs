// Wake-pump tests: the TCP frame codec is UNCHANGED, and the connected-
// state per-user backlog bound (B12, <=16 frames of 20 ms) drops and counts.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { encodeFrame, createFrameParser, createPump } from '../src/recv.js';

test('frame codec round-trips arbitrary user ids and PCM, even split mid-frame', () => {
  const pcm = Buffer.alloc(3840, 7);
  const frame = encodeFrame('user-123', pcm);
  const seen = [];
  const feed = createFrameParser((userId, gotPcm) => seen.push([userId, gotPcm]));
  feed(frame.subarray(0, 10));
  feed(frame.subarray(10, 100));
  feed(frame.subarray(100));
  assert.equal(seen.length, 1);
  assert.equal(seen[0][0], 'user-123');
  assert.deepEqual(seen[0][1], pcm);
});

// A fake TCP socket whose write() never flushes until told to.
function fakeSocket() {
  const socket = new EventEmitter();
  socket.writes = [];
  socket.callbacks = [];
  socket.write = (frame, cb) => {
    socket.writes.push(frame);
    socket.callbacks.push(cb);
    return false;
  };
  socket.flushOne = () => socket.callbacks.shift()?.();
  socket.destroy = () => socket.emit('close');
  return socket;
}

test('per-user backlog beyond 16 frames is dropped and counted; other users unaffected', () => {
  const socket = fakeSocket();
  const pump = createPump({ connectImpl: () => socket });
  const pcm = Buffer.alloc(3840);

  pump(pcm, 'u1'); // no socket yet -> starts the handshake, frame held in pending
  socket.emit('connect'); // handshake completes, pending flushes
  assert.equal(socket.writes.length, 1);

  for (let i = 0; i < 15; i += 1) pump(pcm, 'u1'); // backlog now 16
  assert.equal(socket.writes.length, 16);
  assert.deepEqual(pump.stats(), { backlog_dropped: 0, no_listener_dropped: 0 });

  pump(pcm, 'u1'); // 17th unflushed frame -> dropped + counted
  pump(pcm, 'u1');
  assert.equal(socket.writes.length, 16);
  assert.equal(pump.stats().backlog_dropped, 2);

  pump(pcm, 'u2'); // a different user has its own budget
  assert.equal(socket.writes.length, 17);

  socket.flushOne(); // one u1 frame drains -> budget frees up
  pump(pcm, 'u1');
  assert.equal(socket.writes.length, 18);
  assert.equal(pump.stats().backlog_dropped, 2);
});

test('handshake overflow drops the oldest pending frame and counts it', () => {
  const dead = new EventEmitter();
  dead.write = () => false;
  dead.destroy = () => dead.emit('close');
  const pump = createPump({ connectImpl: () => dead, maxPending: 2 });
  const pcm = Buffer.alloc(3840);
  pump(pcm, 'u1'); // handshake starts, frame held
  pump(pcm, 'u1');
  pump(pcm, 'u1'); // exceeds the pending cap -> oldest dropped + counted
  assert.equal(pump.stats().no_listener_dropped, 1);
  assert.equal(pump.stats().backlog_dropped, 0);
});

test('close during the TCP handshake destroys the connecting socket (2026-09-16 audit N-5)', () => {
  const socket = fakeSocket();
  const pump = createPump({ connectImpl: () => socket });
  pump(Buffer.alloc(3840), 'u1'); // starts the handshake; frame held in pending

  let destroyed = 0;
  const originalDestroy = socket.destroy;
  socket.destroy = () => {
    destroyed += 1;
    originalDestroy();
  };
  pump.close(); // connection teardown while the handshake is still open
  assert.equal(destroyed, 1);

  socket.emit('connect'); // a late connect must not resurrect the closed pump
  assert.equal(socket.writes.length, 0);
});

