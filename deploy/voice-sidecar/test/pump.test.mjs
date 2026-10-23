// Wake-pump tests: the TCP frame codec is UNCHANGED, and the connected-
// state per-user backlog bound (B12, <=16 frames of 20 ms) drops and counts.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { encodeFrame, encodeHello, createFrameParser } from '../src/codec.mjs';
import { createPump } from '../src/pump.mjs';

const TOKEN = 'test-bridge-token';

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
const GUILD = '111111111111111111';

function fakeSocket() {
  const socket = new EventEmitter();
  socket.writes = [];
  socket.callbacks = [];
  socket.write = (frame, cb) => {
    socket.writes.push(frame);
    // Only when one was GIVEN. The hello is written without a callback, and
    // pushing an undefined one made `flushOne()` drain the hello's slot
    // instead of a real frame's — so a user's backlog never freed up.
    if (cb) socket.callbacks.push(cb);
    return false;
  };
  socket.flushOne = () => socket.callbacks.shift()?.();
  socket.destroy = () => socket.emit('close');
  return socket;
}

test('per-user backlog beyond 16 frames is dropped and counted; other users unaffected', () => {
  const socket = fakeSocket();
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => socket });
  const pcm = Buffer.alloc(3840);

  pump(pcm, 'u1'); // no socket yet -> starts the handshake, frame held in pending
  socket.emit('connect'); // handshake completes: hello first, then pending
  const HELLO = 1; // writes[0] is the guild announcement, not a user's audio
  assert.equal(socket.writes.length, HELLO + 1);

  for (let i = 0; i < 15; i += 1) pump(pcm, 'u1'); // backlog now 16
  assert.equal(socket.writes.length, HELLO + 16);
  assert.deepEqual(pump.stats(), { backlog_dropped: 0, no_listener_dropped: 0 });

  pump(pcm, 'u1'); // 17th unflushed frame -> dropped + counted
  pump(pcm, 'u1');
  assert.equal(socket.writes.length, HELLO + 16);
  assert.equal(pump.stats().backlog_dropped, 2);

  pump(pcm, 'u2'); // a different user has its own budget
  assert.equal(socket.writes.length, HELLO + 17);

  socket.flushOne(); // one u1 frame drains -> budget frees up
  pump(pcm, 'u1');
  assert.equal(socket.writes.length, HELLO + 18);
  assert.equal(pump.stats().backlog_dropped, 2);
});

test('handshake overflow drops the oldest pending frame and counts it', () => {
  const dead = new EventEmitter();
  dead.write = () => false;
  dead.destroy = () => dead.emit('close');
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => dead, maxPending: 2 });
  const pcm = Buffer.alloc(3840);
  pump(pcm, 'u1'); // handshake starts, frame held
  pump(pcm, 'u1');
  pump(pcm, 'u1'); // exceeds the pending cap -> oldest dropped + counted
  assert.equal(pump.stats().no_listener_dropped, 1);
  assert.equal(pump.stats().backlog_dropped, 0);
});

test('close during the TCP handshake destroys the connecting socket (2026-09-16 audit N-5)', () => {
  const socket = fakeSocket();
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => socket });
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
  // Not even the hello: a closed pump writes NOTHING, and a hello on a dead
  // socket would announce a guild for a connection that carries no audio.
  assert.equal(socket.writes.length, 0);
});


// --- the guild HELLO (multi-guild, 2026-09-17) -------------------------------
// THE GAP THESE CLOSE: suppressing the hello reddened NOTHING when the
// falsification probe tried it, because `createPump` lived in recv.js behind
// the discord.js import and no test could reach it. Splitting it out is what
// made these possible.

test('the hello is written on connect, BEFORE any held audio', () => {
  // Order is the point. A gate that received audio before knowing the guild
  // would have to buffer it or drop it, and dropping an utterance's onset is
  // exactly what the pending buffer exists to prevent.
  const socket = fakeSocket();
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => socket });

  pump(Buffer.alloc(3840), 'u1'); // held: no socket yet
  socket.emit('connect');

  assert.equal(socket.writes.length, 2);
  assert.deepEqual(socket.writes[0], encodeHello(GUILD, TOKEN));
  assert.deepEqual(socket.writes[1], encodeFrame('u1', Buffer.alloc(3840)));
});

test('the hello is re-sent on every reconnect', () => {
  // A gate restart drops the connection; the next one is a NEW connection and
  // carries no memory of which guild the old one was for.
  let socket = fakeSocket();
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => socket });

  pump(Buffer.alloc(3840), 'u1');
  socket.emit('connect');
  assert.deepEqual(socket.writes[0], encodeHello(GUILD, TOKEN));

  socket.emit('close');            // the gate went away
  const reconnected = fakeSocket();
  socket = reconnected;
  pump(Buffer.alloc(3840), 'u1');  // re-opens
  reconnected.emit('connect');

  assert.deepEqual(reconnected.writes[0], encodeHello(GUILD, TOKEN));
});

test('the hello does not enter a user backlog', () => {
  // It is not a user's audio. Counting it against `u1` would let one frame of
  // real speech past the per-user bound go missing instead.
  const socket = fakeSocket();
  const pump = createPump({ guildId: GUILD, token: TOKEN, connectImpl: () => socket });

  pump(Buffer.alloc(3840), 'u1');
  socket.emit('connect');
  for (let i = 0; i < 15; i += 1) pump(Buffer.alloc(3840), 'u1');

  // 1 hello + 16 audio frames, none dropped: the bound counted audio only.
  assert.equal(socket.writes.length, 17);
  assert.deepEqual(pump.stats(), { backlog_dropped: 0, no_listener_dropped: 0 });
});

test('a pump without a guildId is refused at construction', () => {
  // THE SHAPE THE SUITE USED TO NORMALISE. The hello was written behind
  // `if (guildId)`, so a falsy one produced a pump whose frames the gate drops
  // wholesale — a failure in another container, with nothing here to point at
  // it. Every test above built exactly that pump and passed.
  //
  // Refused at construction, not at connect: a throw inside the socket's
  // 'connect' handler is an uncaught exception that takes the process down.
  assert.throws(() => createPump({ connectImpl: () => fakeSocket() }), /needs a guildId/);
  assert.throws(
    () => createPump({ guildId: '', connectImpl: () => fakeSocket() }),
    /needs a guildId/,
  );
});

test('a pump without the service token is refused at construction', () => {
  // The gate drops a hello it cannot authenticate, so a tokenless pump would
  // be a silent audio sink in another container. Refused here, like a guildless one.
  assert.throws(
    () => createPump({ guildId: GUILD, connectImpl: () => fakeSocket() }),
    /needs the service token/,
  );
});
