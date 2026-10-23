// Wake-gate seam CODEC — the JS half of `deploy/voice-wake/wakegate/codec.py`.
// Read that file's docstring beside this one: they are two implementations of
// ONE wire format and a change to either is a change to both.
//
// Frame:  [u32be payload_len][u8 user_id_len][user_id utf8][pcm bytes]
// Hello:  [u32be payload_len][u8 0][guild_id utf8]["\n"][token utf8]
//
// See TRANSPORT.md for why TCP with a length prefix.
//
// EXTRACTED from recv.js 2026-09-17, when the hello frame was added. recv.js
// imports discord.js at module scope, so everything in it — including this
// pure byte layout — was unreachable to a test without a node_modules tree.
// `pump.test.mjs` had therefore not run locally in some time. A wire format
// whose two halves must agree is exactly the code that has to be testable on
// both sides.

// The HELLO a pump writes once on connect, naming the guild this connection
// carries (multi-guild, 2026-09-17). A user_id length of ZERO discriminates
// it: Discord never issues an empty snowflake, so the existing length byte
// does the work and the audio frame layout is untouched. Announced once
// rather than per frame because a pump is created per join, so a connection
// belongs to exactly one guild for its lifetime — and 20 ms of audio keeps
// its 2-byte header.
// The token (NORA_BRIDGE_TOKEN) follows the guild after a newline. A guild id is
// digits and cannot contain one, so the first newline is always the separator;
// the wake gate checks the token before it trusts anything else in the hello.
export function encodeHello(guildId, token) {
  const id = Buffer.from(String(guildId), 'utf8');
  if (!id.length) throw new Error('a hello frame needs a guildId');
  if (id.length > 255) throw new Error('guildId too long for frame');
  if (id.includes(0x0a)) throw new Error('guildId must not contain a newline');
  const secret = Buffer.from(String(token ?? ''), 'utf8');
  if (!secret.length) throw new Error('a hello frame needs the service token');
  if (secret.length > 256) throw new Error('service token too long for hello');
  const payload = Buffer.concat([Buffer.from([0]), id, Buffer.from('\n'), secret]);
  const header = Buffer.alloc(4);
  header.writeUInt32BE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

export function encodeFrame(userId, pcm) {
  const id = Buffer.from(String(userId), 'utf8');
  // An empty userId would encode as the HELLO marker and be swallowed by the
  // parser as a guild announcement.
  if (!id.length) throw new Error('userId must not be empty (0 is the hello marker)');
  if (id.length > 255) throw new Error('userId too long for frame');
  const payload = Buffer.concat([Buffer.from([id.length]), id, pcm]);
  const header = Buffer.alloc(4);
  header.writeUInt32BE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

/**
 * Incremental parser for the frame format above. Feed it arbitrary TCP
 * chunks; it calls onFrame(userId, pcmBuffer) per complete AUDIO frame.
 *
 * A HELLO is consumed, not emitted — it carries a guild id and the token, not
 * audio. When `onHello` is given it is called with both. This parser does not
 * judge the token: the wake gate (`codec.py`) is the party that checks it.
 * Mirrors `wakegate/codec.py`'s `FrameParser`, which swallows it into `guild_id`:
 * the two halves of one wire format must not disagree about what a frame IS,
 * and for one commit this one emitted the hello as a frame whose speaker was
 * the empty string.
 */
export function createFrameParser(onFrame, onHello = () => {}) {
  let buf = Buffer.alloc(0);
  return function feed(chunk) {
    buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
    for (;;) {
      if (buf.length < 4) return;
      const payloadLen = buf.readUInt32BE(0);
      if (buf.length < 4 + payloadLen) return;
      const payload = buf.subarray(4, 4 + payloadLen);
      // A zero-length payload has no length byte at all. JS read `payload[0]`
      // as `undefined` and fell through to emit a phantom frame for the empty
      // speaker; Python raised IndexError, which its caller turns into a
      // dropped CONNECTION. Two implementations of one format disagreeing
      // about what a frame is — and neither failing closed.
      if (payloadLen < 1) {
        buf = buf.subarray(4 + payloadLen);
        continue;
      }
      const idLen = payload[0];
      if (idLen === 0) {
        const body = payload.subarray(1);
        const sep = body.indexOf(0x0a);
        const guildId = (sep < 0 ? body : body.subarray(0, sep)).toString('utf8');
        const token = sep < 0 ? '' : body.subarray(sep + 1).toString('utf8');
        buf = buf.subarray(4 + payloadLen);
        if (guildId) onHello(guildId, token);
        continue;
      }
      const userId = payload.subarray(1, 1 + idLen).toString('utf8');
      const pcm = payload.subarray(1 + idLen);
      buf = buf.subarray(4 + payloadLen);
      onFrame(userId, pcm);
    }
  };
}
