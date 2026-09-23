/**
 * Tests for the pure helpers behind deploy/whatsapp-bridge/bridge.js.
 *
 * Covers the contract-pinned behaviors: JID parsing/resolution, the LID
 * reverse map, drop-and-count buffering, message-event extraction (mentions,
 * quotes, media descriptors, MESSAGE_EDIT), and reconnect backoff. The
 * daemon itself (socket + HTTP) is not imported here — it binds a port and
 * opens a Baileys socket at module load.
 *
 * Run: npm test   (node --test)
 */

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

import {
  buildLidMap,
  buildMediaPayload,
  createEventBuffer,
  createRecentMessageCache,
  createReconnectScheduler,
  extractMessageEvent,
  invertLidMap,
  isGroupJid,
  isLidJid,
  isValidTargetJid,
  jidUserDigits,
  normalizeJid,
  redactJid,
  resolveIdentity,
  timestampMs,
  deriveEditKey,
  decryptSecretEdit,
  SECRET_ENC_MESSAGE_EDIT,
} from './bridge_helpers.js';
import { proto } from '@whiskeysockets/baileys';

// -- JID parsing --------------------------------------------------------------

test('normalizeJid strips the multi-device suffix from the user part', () => {
  assert.equal(normalizeJid('85290000100:10@s.whatsapp.net'), '85255080100@s.whatsapp.net');
  assert.equal(normalizeJid('60000000000001:3@lid'), '60000000000001@lid');
  assert.equal(normalizeJid('176569000000000000@g.us'), '176569000000000000@g.us');
  assert.equal(normalizeJid(''), '');
  assert.equal(normalizeJid(null), '');
});

test('jidUserDigits returns bare digits, no device suffix or domain', () => {
  assert.equal(jidUserDigits('85290000100:10@s.whatsapp.net'), '85290000100');
  assert.equal(jidUserDigits('60000000000001@lid'), '60000000000001');
  assert.equal(jidUserDigits(''), '');
});

test('jid classification', () => {
  assert.equal(isGroupJid('176569000000000000@g.us'), true);
  assert.equal(isGroupJid('85255080100@s.whatsapp.net'), false);
  assert.equal(isLidJid('60000000000001@lid'), true);
  assert.equal(isLidJid('85255080100@s.whatsapp.net'), false);
});

test('isValidTargetJid accepts person/group/lid and rejects the rest', () => {
  assert.equal(isValidTargetJid('85255080100@s.whatsapp.net'), true);
  assert.equal(isValidTargetJid('176569000000000000@g.us'), true);
  assert.equal(isValidTargetJid('60000000000001@lid'), true);
  assert.equal(isValidTargetJid('status@broadcast'), false);
  assert.equal(isValidTargetJid('176569@newsletter'), false);
  assert.equal(isValidTargetJid('not-a-jid'), false);
  assert.equal(isValidTargetJid(''), false);
});

test('timestampMs coerces numbers, numeric strings, and Long-likes to ms', () => {
  assert.equal(timestampMs(1756963200), 1756963200000);
  assert.equal(timestampMs('1756963200'), 1756963200000);
  assert.equal(timestampMs({ toNumber: () => 1756963200 }), 1756963200000);
  assert.equal(timestampMs(null), null);
  assert.equal(timestampMs(undefined), null);
});

// -- LID reverse map ------------------------------------------------------------

test('buildLidMap reads lid-mapping-<phone>.json files into a reverse map', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'nora-wa-lid-'));
  writeFileSync(path.join(dir, 'lid-mapping-85290000100.json'), JSON.stringify('60000000000001'));
  writeFileSync(path.join(dir, 'lid-mapping-85202411020.json'), JSON.stringify(['14924244741244']));
  writeFileSync(path.join(dir, 'creds.json'), '{}'); // ignored
  writeFileSync(path.join(dir, 'lid-mapping-broken.json'), 'not json'); // ignored
  writeFileSync(path.join(dir, 'lid-mapping-.json'), JSON.stringify('9')); // no phone digits — ignored

  const map = buildLidMap(dir);
  assert.equal(map['60000000000001'], '85290000100');
  assert.equal(map['14924244741244'], '85202411020');
  assert.equal(Object.keys(map).length, 2);
});

test('buildLidMap tolerates a missing session dir', () => {
  assert.deepEqual(buildLidMap('/nonexistent/session/dir'), {});
});

test('resolveIdentity maps phone JIDs, known LIDs, unknown LIDs, and groups', () => {
  const lidToPhone = { '60000000000001': '85290000100' };
  const phoneToLid = invertLidMap(lidToPhone);

  assert.deepEqual(resolveIdentity('85202411020:5@s.whatsapp.net', lidToPhone, phoneToLid), {
    phone: '85202411020', lid: null,
  });
  // BOTH directions ride every event: a phone-form JID still carries its lid
  // when the session map knows one, so the adapter's both-forms membership
  // test works whichever spelling Baileys delivered.
  assert.deepEqual(resolveIdentity('85255080100@s.whatsapp.net', lidToPhone, phoneToLid), {
    phone: '85290000100', lid: '60000000000001',
  });
  assert.deepEqual(resolveIdentity('60000000000001@lid', lidToPhone, phoneToLid), {
    phone: '85290000100', lid: '60000000000001',
  });
  assert.deepEqual(resolveIdentity('97399293222369@lid', lidToPhone, phoneToLid), {
    phone: null, lid: '97399293222369',
  });
  assert.deepEqual(resolveIdentity('176569000000000000@g.us', lidToPhone, phoneToLid), {
    phone: null, lid: null,
  });
});

test('invertLidMap is the pointwise inverse and tolerates an empty map', () => {
  assert.deepEqual(invertLidMap({ '60000000000001': '85290000100' }), {
    '85290000100': '60000000000001',
  });
  assert.deepEqual(invertLidMap({}), {});
});

// -- Recent-message cache ---------------------------------------------------------

test('recent-message cache remembers key material and resolves quoted timestamps', () => {
  const cache = createRecentMessageCache(3);
  cache.remember({
    key: { id: 'A', remoteJid: '176569@g.us', participant: '60000000000001@lid', fromMe: false },
    messageTimestamp: 1756963100,
  });

  const hit = cache.get('A');
  assert.equal(hit.chat_jid, '176569@g.us');
  assert.equal(hit.sender_jid, '60000000000001@lid');
  assert.equal(hit.from_me, false);
  assert.equal(hit.timestamp_ms, 1756963100000);
  assert.deepEqual(hit.key, {
    id: 'A', remoteJid: '176569@g.us', fromMe: false, participant: '60000000000001@lid',
  });
  assert.equal(cache.get('nope'), null);
});

test('recent-message cache is bounded, evicting oldest first', () => {
  const cache = createRecentMessageCache(2);
  for (const id of ['A', 'B', 'C']) {
    cache.remember({ key: { id, remoteJid: '1@s.whatsapp.net', fromMe: true }, messageTimestamp: 1 });
  }
  assert.equal(cache.size, 2);
  assert.equal(cache.get('A'), null);
  assert.ok(cache.get('B'));
  assert.ok(cache.get('C'));
});

// -- Drop-and-count buffer ----------------------------------------------------------

test('event buffer drops the OLDEST on overflow and counts — never silently', () => {
  const drops = [];
  const buf = createEventBuffer(3, { onDrop: (n) => drops.push(n) });
  buf.push({ id: 1 });
  buf.push({ id: 2 });
  buf.push({ id: 3 });
  assert.equal(buf.size, 3);
  assert.equal(buf.dropped, 0);

  buf.push({ id: 4 });
  buf.push({ id: 5 });
  assert.equal(buf.size, 3, 'cap holds');
  assert.equal(buf.dropped, 2, 'every overflow counted');
  assert.deepEqual(drops, [1, 2], 'drop callback fired per drop');
  assert.deepEqual(buf.snapshot().map((e) => e.id), [3, 4, 5], 'oldest dropped first');

  // The counter is permanent: clearing the replayed events does not reset it.
  buf.clear();
  assert.equal(buf.size, 0);
  assert.equal(buf.dropped, 2);
});

// -- Reconnect scheduler ----------------------------------------------------------------

test('reconnect scheduler backs off exponentially with jitter, capped', async () => {
  const timers = [];
  const logs = [];
  let attempts = 0;
  const sched = createReconnectScheduler(
    async () => { attempts += 1; throw new Error('boom'); },
    {
      baseDelayMs: 1000,
      maxDelayMs: 8000,
      log: (line) => logs.push(line),
      setTimeoutFn: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
      clearTimeoutFn: () => {},
      rand: () => 0.5, // jitter factor exactly 0.75
    },
  );

  sched.scheduleReconnect();
  assert.equal(timers[0].ms, 750); // 1000 * 0.75
  timers[0].fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  assert.equal(timers[1].ms, 1500); // 2000 * 0.75
  timers[1].fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  assert.equal(timers[2].ms, 3000); // 4000 * 0.75
  timers[2].fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  assert.equal(timers[3].ms, 6000); // capped at 8000 * 0.75
  timers[3].fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));

  assert.equal(timers[4].ms, 6000, 'cap holds on subsequent failures');
  assert.equal(attempts, 4);
  assert.equal(logs.length, 5, 'one schedule log + one retry log per failure');
});

test('reconnect scheduler dedupes pending schedules and resets on open', async () => {
  const timers = [];
  const sched = createReconnectScheduler(async () => {}, {
    log: () => {},
    setTimeoutFn: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeoutFn: () => {},
    rand: () => 0.5,
  });
  sched.scheduleReconnect();
  sched.scheduleReconnect();
  assert.equal(timers.length, 1, 'a pending timer suppresses duplicates');
  timers[0].fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
  assert.equal(timers.length, 1, 'success does not reschedule');
  sched.reset();
  assert.equal(sched.attempts, 0);
});

// -- Message event extraction ------------------------------------------------------------

const LID_MAP = { '60000000000001': '85290000100' };

test('extracts a group text message with LID sender resolved to phone', () => {
  const event = extractMessageEvent({
    key: { id: '3EB0F1', remoteJid: '176569000000000000@g.us', participant: '60000000000001:2@lid', fromMe: false },
    pushName: 'Alice',
    messageTimestamp: 1756963200,
    message: { conversation: 'hello' },
  }, { lidToPhone: LID_MAP });

  assert.deepEqual(event, {
    type: 'message',
    id: '3EB0F1',
    chat_jid: '176569000000000000@g.us',
    is_group: true,
    from_me: false,
    sender_jid: '60000000000001@lid',
    sender_phone: '85290000100',
    sender_lid: '60000000000001',
    push_name: 'Alice',
    text: 'hello',
    timestamp_ms: 1756963200000,
    mentioned: [],
    quoted: null,
    media: [],
    edit_of: null,
  });
});

test('1:1 sender is the chat JID; mentions resolve to phone and lid forms', () => {
  const event = extractMessageEvent({
    key: { id: 'M1', remoteJid: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963200,
    message: {
      extendedTextMessage: {
        text: 'hi @alice',
        contextInfo: {
          mentionedJid: ['60000000000001:1@lid', '85239145793@s.whatsapp.net'],
        },
      },
    },
  }, { lidToPhone: LID_MAP });

  assert.equal(event.is_group, false);
  assert.equal(event.sender_jid, '85202411020@s.whatsapp.net');
  assert.equal(event.sender_phone, '85202411020');
  assert.equal(event.sender_lid, null);
  assert.deepEqual(event.mentioned, [
    { jid: '60000000000001@lid', phone: '85290000100', lid: '60000000000001' },
    { jid: '85239145793@s.whatsapp.net', phone: '85239145793', lid: null },
  ]);
});

test('quoted messages carry participant resolution and cache-resolved timestamp', () => {
  const cache = createRecentMessageCache(10);
  cache.remember({
    key: { id: '3EB0AA', remoteJid: '176569@g.us', participant: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963100,
  });

  const event = extractMessageEvent({
    key: { id: 'Q1', remoteJid: '176569@g.us', participant: '60000000000001@lid', fromMe: false },
    messageTimestamp: 1756963200,
    message: {
      extendedTextMessage: {
        text: 'reply',
        contextInfo: {
          stanzaId: '3EB0AA',
          participant: '85202411020:7@s.whatsapp.net',
          quotedMessage: { conversation: 'earlier message' },
        },
      },
    },
  }, { lidToPhone: LID_MAP, recentCache: cache });

  assert.deepEqual(event.quoted, {
    id: '3EB0AA',
    participant_jid: '85202411020@s.whatsapp.net',
    participant_phone: '85202411020',
    participant_lid: null,
    timestamp_ms: 1756963100000,
    text: 'earlier message',
  });
});

test('quoted timestamp is null when the parent predates the cache', () => {
  const event = extractMessageEvent({
    key: { id: 'Q2', remoteJid: '176569@g.us', participant: '1@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963200,
    message: {
      extendedTextMessage: {
        text: 'reply to old',
        contextInfo: {
          stanzaId: 'ANCIENT',
          participant: '2@s.whatsapp.net',
          quotedMessage: { conversation: 'old' },
        },
      },
    },
  }, { recentCache: createRecentMessageCache(10) });

  assert.equal(event.quoted.id, 'ANCIENT', 'reply relation survives');
  assert.equal(event.quoted.timestamp_ms, null, 'parent pointer honestly null');
});

test('voice note surfaces as audio media descriptor with ptt flag — no bytes', () => {
  const event = extractMessageEvent({
    key: { id: 'V1', remoteJid: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963200,
    message: {
      audioMessage: {
        mimetype: 'audio/ogg; codecs=opus',
        ptt: true,
        seconds: 4,
        fileLength: 12345,
      },
    },
  }, {});

  assert.equal(event.text, null);
  assert.deepEqual(event.media, [{
    kind: 'audio',
    mimetype: 'audio/ogg; codecs=opus',
    voice_note: true,
    seconds: 4,
    size: 12345,
    caption: null,
    filename: null,
  }]);
  assert.ok(!('url' in event.media[0]) && !('data' in event.media[0]), 'no media custody');
});

test('document media keeps filename and caption; caption becomes text', () => {
  const event = extractMessageEvent({
    key: { id: 'D1', remoteJid: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963200,
    message: {
      documentMessage: {
        mimetype: 'application/pdf',
        fileName: 'report.pdf',
        fileLength: { toNumber: () => 99999 },
        caption: 'see attached',
      },
    },
  }, {});

  assert.equal(event.text, 'see attached');
  assert.deepEqual(event.media, [{
    kind: 'document',
    mimetype: 'application/pdf',
    voice_note: false,
    seconds: null,
    size: 99999,
    caption: 'see attached',
    filename: 'report.pdf',
  }]);
});

test('MESSAGE_EDIT protocol message becomes an event with edit_of set', () => {
  const event = extractMessageEvent({
    key: { id: 'E1', remoteJid: '176569@g.us', participant: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963250,
    message: {
      protocolMessage: {
        type: 'MESSAGE_EDIT',
        key: { id: 'ORIG1', remoteJid: '176569@g.us', fromMe: false },
        editedMessage: { conversation: 'new text' },
      },
    },
  }, {});

  assert.equal(event.edit_of, 'ORIG1');
  assert.equal(event.text, 'new text');
  assert.equal(event.id, 'E1');
});

test('a phone edit wrapped in editedMessage is unwrapped into an edit event', () => {
  // The shape a phone sends (Baileys rc13 normalizeMessageContent unwraps it
  // the same way). Before, this produced NO event at all.
  const event = extractMessageEvent({
    key: { id: 'E2', remoteJid: '176569@g.us', participant: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963250,
    message: {
      editedMessage: {
        message: {
          protocolMessage: {
            type: 14,
            key: { id: 'ORIG2', remoteJid: '176569@g.us', fromMe: false },
            editedMessage: { conversation: 'fixed text' },
            timestampMs: '1756963250123',
          },
        },
      },
    },
  }, {});

  assert.ok(event, 'a wrapped edit must not be dropped');
  assert.equal(event.edit_of, 'ORIG2');
  assert.equal(event.text, 'fixed text');
  assert.equal(event.timestamp_ms, 1756963250123, 'the edit\'s own millisecond clock');
});

test('non-edit protocol messages and contentless messages produce no event', () => {
  assert.equal(extractMessageEvent({
    key: { id: 'P1', remoteJid: '176569@g.us', participant: '1@s.whatsapp.net' },
    message: { protocolMessage: { type: 'REVOKE', key: { id: 'X' } } },
  }, {}), null);

  assert.equal(extractMessageEvent({
    key: { id: 'P2', remoteJid: '176569@g.us', participant: '1@s.whatsapp.net' },
    message: { senderKeyDistributionMessage: {} },
  }, {}), null);

  assert.equal(extractMessageEvent({ key: { id: 'P3' }, message: null }, {}), null);
});

test('ephemeral wrappers are unwrapped before extraction', () => {
  const event = extractMessageEvent({
    key: { id: 'W1', remoteJid: '85202411020@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1756963200,
    message: { ephemeralMessage: { message: { conversation: 'disappearing' } } },
  }, {});
  assert.equal(event.text, 'disappearing');
});

// -- Outbound media payload ----------------------------------------------------------------

test('buildMediaPayload shapes per media_type with mimetype from filename', () => {
  const buf = Buffer.from('bytes');
  assert.deepEqual(buildMediaPayload({ mediaType: 'image', buffer: buf, filename: 'p.png', caption: 'look' }), {
    image: buf, caption: 'look', mimetype: 'image/png',
  });
  assert.deepEqual(buildMediaPayload({ mediaType: 'video', buffer: buf, filename: 'v.mp4' }), {
    video: buf, mimetype: 'video/mp4',
  });
  assert.deepEqual(buildMediaPayload({ mediaType: 'audio', buffer: buf }), {
    audio: buf, mimetype: 'audio/ogg; codecs=opus',
  });
  assert.deepEqual(buildMediaPayload({ mediaType: 'sticker', buffer: buf }), {
    sticker: buf, mimetype: 'image/webp',
  });
  assert.deepEqual(buildMediaPayload({ mediaType: 'document', buffer: buf, filename: 'r.pdf' }), {
    document: buf, fileName: 'r.pdf', mimetype: 'application/pdf',
  });
  assert.equal(buildMediaPayload({ mediaType: 'carrier-pigeon', buffer: buf }), null);
});

// -- Logging redaction ------------------------------------------------------------------------

test('redactJid keeps only last-4 digits plus domain', () => {
  assert.equal(redactJid('85290000100:10@s.whatsapp.net'), '…0100@s.whatsapp.net');
  assert.equal(redactJid('60000000000001@lid'), '…7522@lid');
  assert.equal(redactJid(''), '');
});

// -- Secret-encrypted edits (a phone's edit since 2026) ----------------------
// Vector generated by whatsmeow ITSELF (generateMsgSecretKey + gcmutil.Encrypt,
// go.mau.fi/whatsmeow msgsecret.go) for: secret 00..1f, original id
// 3EB0ORIGID, original sender and editor 149242447412@lid, iv 0a..15,
// plaintext = Message{conversation:"hello"}. Matching it byte for byte is what
// says this port derives the same key WhatsApp's peers do.
const WM_SECRET = Buffer.from('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f', 'hex');
const WM_KEY = 'f646ec079a5980aa3ab84d2562ab32d51beae16a1b0f61d66c20e63c91068684';
const WM_IV = Buffer.from('0a0b0c0d0e0f101112131415', 'hex');
const WM_CT = Buffer.from('f898ea0de5de134a60b3e93b344bc18468ee4268f31822', 'hex');

test('the edit key matches whatsmeow byte for byte', () => {
  const key = deriveEditKey(WM_SECRET, '3EB0ORIGID', '149242447412@lid', '149242447412@lid');
  assert.equal(key.toString('hex'), WM_KEY);
});

test('the key orders original sender before editor, both without device, as whatsmeow does', () => {
  // A second whatsmeow vector with the two sides DIFFERENT (the editor's JID
  // carries a device part, ":3", which whatsmeow strips): swapping them, or
  // keeping the device, gives a different key.
  const key = deriveEditKey(WM_SECRET, '3EB0ORIGID', '149242447412@lid', '85202411020@s.whatsapp.net');
  assert.equal(key.toString('hex'), 'b87c3f1eaf124da257537ee2bb7d26c8c9f847813aa7caa3698b2077881154b8');
});

function secretEditFixture({ originalParticipant = '149242447412@lid', lidToPhone = {} } = {}) {
  const cache = createRecentMessageCache();
  cache.remember({
    key: { id: '3EB0ORIGID', remoteJid: '176569@g.us', participant: originalParticipant, fromMe: false },
    messageTimestamp: 1756963000,
    message: { conversation: 'helo', messageContextInfo: { messageSecret: WM_SECRET } },
  });
  const edit = {
    key: { id: '3EB0EDIT', remoteJid: '176569@g.us', participant: '149242447412@lid', fromMe: false },
    messageTimestamp: 1756963100,
    message: {
      messageContextInfo: {},
      secretEncryptedMessage: {
        targetMessageKey: { id: '3EB0ORIGID', remoteJid: '176569@g.us', participant: originalParticipant, fromMe: false },
        encPayload: WM_CT, encIv: WM_IV, secretEncType: SECRET_ENC_MESSAGE_EDIT,
      },
    },
  };
  return { cache, edit, lidToPhone };
}

test('a secret-encrypted phone edit decrypts into an edit event', () => {
  const { cache, edit } = secretEditFixture();
  const opened = decryptSecretEdit(edit, { recentCache: cache, decode: (b) => proto.Message.decode(b) });
  assert.ok(opened.msg, `not decrypted: ${opened.reason}`);
  const event = extractMessageEvent(opened.msg, {});
  assert.equal(event.edit_of, '3EB0ORIGID');
  assert.equal(event.text, 'hello');
  assert.equal(event.id, '3EB0EDIT');
  assert.equal(event.timestamp_ms, 1756963100000);
});

test('it decrypts when the original was recorded under the phone form of the same person', () => {
  // WhatsApp is mid-migration to LIDs: the cache may hold the phone JID while
  // the key was derived with the LID. Every known form is tried.
  const { cache, edit } = secretEditFixture({ originalParticipant: '85202411020@s.whatsapp.net' });
  const opened = decryptSecretEdit(edit, {
    recentCache: cache, decode: (b) => proto.Message.decode(b),
    lidToPhone: { '149242447412': '85202411020' }, phoneToLid: { '85202411020': '149242447412' },
  });
  assert.ok(opened.msg, `not decrypted: ${opened.reason}`);
});

test('an edit of a message the bridge holds no secret for says so', () => {
  const { edit } = secretEditFixture();
  const opened = decryptSecretEdit(edit, { recentCache: createRecentMessageCache(), decode: (b) => proto.Message.decode(b) });
  assert.equal(opened.reason, 'secret_unknown');
});

test('a wrong secret never yields a message', () => {
  const { cache, edit } = secretEditFixture();
  cache.get('3EB0ORIGID').message_secret = Buffer.alloc(32, 7);
  const opened = decryptSecretEdit(edit, { recentCache: cache, decode: (b) => proto.Message.decode(b) });
  assert.equal(opened.reason, 'undecryptable');
});
