/**
 * Normalization-parity tests: the JS `parse_event` port must produce the
 * `message_to_dict`-shaped NormalizedMessage the app rehydrates, with
 * transition routing fields (`kind`, `upstream`) for envelope identity.
 * Drop rules mirror `nora/platform/whatsapp/parser.py`.
 *
 * Run: node --test normalize.test.mjs
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { parseEvent } from './normalize.js';
import {
  conversationIdFor, isGroupJid, parseJid, phoneForm, lidForm,
} from './wa_jid.js';
import { createRecentMessageCache, extractMessageEvent, normalizeJid } from './bridge_helpers.js';

// -- JID helpers (port of nora/platform/whatsapp/jid.py) -------------------------

test('parseJid splits user, device, server — matching JidParts', () => {
  assert.deepEqual(parseJid('85290000100:7@s.whatsapp.net'), {
    user: '85290000100', device: '7', server: 's.whatsapp.net',
  });
  assert.deepEqual(parseJid('1234@lid'), {
    user: '1234', device: null, server: 'lid',
  });
  assert.equal(parseJid('not-a-jid'), null);
});

test('normalizeJid strips the device suffix on JIDs that carry one', () => {
  assert.equal(normalizeJid('85290000100:9@s.whatsapp.net'), '85290000100@s.whatsapp.net');
  // No '@' at all → the raw string, no default server invented.
  assert.equal(normalizeJid('85290000100:9'), '85290000100:9');
  assert.equal(normalizeJid(''), '');
});

test('phoneForm/lidForm follow jid.py (strip +, no digit validation)', () => {
  assert.equal(phoneForm('+85290000100'), '+85290000100');
  assert.equal(phoneForm('85290000100'), '+85290000100');
  assert.equal(lidForm('1234'), '1234');
  assert.equal(lidForm('+1234'), '1234');
});

test('isGroupJid only honors the group domain', () => {
  assert.equal(isGroupJid('152099@g.us'), true);
  assert.equal(isGroupJid('x@g.us.invalid'), false);
  assert.equal(isGroupJid('1@s.whatsapp.net'), false);
});

test('conversationIdFor mirrors jid.py exactly', () => {
  assert.equal(conversationIdFor('152099@g.us', null), '152099@g.us');
  assert.equal(conversationIdFor('85290000100@s.whatsapp.net', '+85290000100'), '85290000100@s.whatsapp.net');
  // Phone resolved → phone JID even when chat was LID; unresolved LID stays.
  assert.equal(conversationIdFor('1234@lid', '+85290000100'), '85290000100@s.whatsapp.net');
  assert.equal(conversationIdFor('1234@lid', null), '1234@lid');
});

// -- parse_event: messages -----------------------------------------------------

const MSG = {
  type: 'message', id: '3EB0F1',
  chat_jid: '152099@g.us', is_group: true, from_me: false,
  sender_jid: '85290000100@s.whatsapp.net', sender_phone: '+85290000100', sender_lid: null,
  push_name: 'Someone', text: 'héllo 👍', timestamp_ms: 1730300000000,
  mentioned: [], quoted: null, media: [], edit_of: null,
};

test('a text turn produces the NormalizedMessage dict + routing fields', () => {
  const { kind, upstream, msg } = parseEvent(MSG);
  assert.equal(kind, 'message');
  assert.equal(upstream, 'm:3EB0F1:+85290000100');
  assert.equal(msg.platform, 'whatsapp');
  assert.equal(msg.conversation_id, '152099@g.us');
  assert.equal(msg.conversation_type, 'group');
  assert.equal(msg.sender_id, '+85290000100');
  assert.equal(msg.sender_name, 'Someone');
  assert.equal(msg.timestamp, 1730300000000);
  assert.equal(msg.body, 'héllo 👍');
  assert.equal(msg.message_id, '3EB0F1');
  assert.equal(msg.quote, null);
  assert.equal(msg.thread, '');
  assert.equal(msg.edit_of, null);
  assert.equal(msg.touch, null);
  assert.deepEqual(msg.media, []);
  assert.deepEqual(msg.mentions, []);
});

test('DM conversations use the phone-JID form', () => {
  const { msg } = parseEvent({
    ...MSG, chat_jid: '85290000100@s.whatsapp.net', is_group: false, is_group_name: undefined,
    text: 'hi',
  });
  assert.equal(msg.conversation_type, 'dm');
  assert.equal(msg.conversation_id, '85290000100@s.whatsapp.net');
});

test('unresolved LID rooms keep the lid JID conversation id', () => {
  const { msg } = parseEvent({
    ...MSG, chat_jid: '1234@lid', is_group: false, text: 'hi',
    sender_jid: '1234@lid', sender_phone: null, sender_lid: '1234',
  });
  assert.equal(msg.conversation_id, '1234@lid');
  assert.equal(msg.sender_id, '1234');
});

test('an edit keeps the NEW body and its own stanza, edit_of names the target', () => {
  const { kind, upstream, msg } = parseEvent({ ...MSG, edit_of: '3EB0F0', text: 'corrected' });
  assert.equal(kind, 'edit');
  assert.equal(upstream, 'e:3EB0F0:3EB0F1');
  assert.equal(msg.body, 'corrected');
  assert.equal(msg.message_id, '3EB0F1');
  assert.deepEqual(msg.edit_of, { message_id: '3EB0F0', sent_ts: null, mentions_known: true });
});

test('a quote survives as a quote — reply-to-Nora detection reads author_id', () => {
  const { msg } = parseEvent({
    ...MSG, text: 'reply',
    quoted: { id: '3EB0F0', participant_jid: '85290000111@s.whatsapp.net',
      participant_phone: '+85290000111', participant_lid: null,
      timestamp_ms: 1730299990000, text: 'orig' },
  });
  assert.deepEqual(msg.quote, {
    quoted_ts: 1730299990000, author_id: '+85290000111', text: 'orig',
  });
  assert.equal(msg.thread, '');
});

test('a quote whose parent predates the cache gets quoted_ts 0, honestly', () => {
  const { msg } = parseEvent({
    ...MSG, text: 'reply',
    quoted: { id: 'OLD', participant_jid: '85290000111@s.whatsapp.net',
      participant_phone: '+85290000111', participant_lid: null,
      timestamp_ms: null, text: null },
  });
  assert.equal(msg.quote.quoted_ts, 0);
});

test('media events render labeled bodies and MediaRef dicts (no filename)', () => {
  const { msg } = parseEvent({
    ...MSG, text: null,
    media: [{ kind: 'image', mimetype: 'image/jpeg', voice_note: false,
      seconds: null, size: 1024, caption: null, filename: 'evil-name.jpg' }],
  });
  assert.equal(msg.body, '[image]');
  assert.deepEqual(msg.media, [{
    platform_id: '', media_type: 'image/jpeg', filename: null, size: 1024,
    voice_note: false, sticker: false, fetch_token: '', on_demand: false,
  }]);
});

test('captioned media keep the caption as body, placeholder still trails', () => {
  const { msg } = parseEvent({
    ...MSG, text: 'nice',
    media: [{ kind: 'image', mimetype: 'image/jpeg', voice_note: false,
      seconds: null, size: 1, caption: 'nice', filename: null }],
  });
  assert.equal(msg.body, 'nice [image]');
});

test('a voice note survives as a bare [voice note] placeholder', () => {
  const { msg } = parseEvent({
    ...MSG, text: null,
    media: [{ kind: 'audio', mimetype: 'audio/ogg; codecs=opus', voice_note: true,
      seconds: 3, size: 99, caption: null, filename: null }],
  });
  assert.equal(msg.body, '[voice note]');
  assert.equal(msg.media[0].voice_note, true);
});

test('stickers label [image] — Python parity: _media_ref leaves sticker=False', () => {
  // `label_for` only says "sticker" when MediaRef.sticker is set, and the
  // parser's `_media_ref` never sets it — the webp mimetype labels 'image'.
  const { msg } = parseEvent({
    ...MSG, text: null,
    media: [{ kind: 'sticker', mimetype: 'image/webp', voice_note: false,
      seconds: null, size: 10, caption: null, filename: null }],
  });
  assert.equal(msg.body, '[image]');
});

// -- parse_event: touches --------------------------------------------------------

test('reactions arrive as touch rows beside the target, never transcript lines', () => {
  const { kind, upstream, msg } = parseEvent({
    type: 'reaction', id: 'R1', chat_jid: '152099@g.us', is_group: true,
    from_me: false, sender_jid: '85290000105@s.whatsapp.net',
    sender_phone: '+85290000105', sender_lid: null, push_name: null,
    target_id: '3EB0F0', target_author: '+85290000111', emoji: '👍',
    removed: false, timestamp_ms: 1730300000000,
  });
  assert.equal(kind, 'reaction');
  assert.equal(upstream, 'r:3EB0F0:R1:+85290000105:add');
  assert.equal(msg.body, '');
  assert.deepEqual(msg.touch, {
    kind: 'reaction', target: { message_id: '3EB0F0', sent_ts: null, mentions_known: true },
    target_author: '+85290000111', emoji: '👍', removed: false,
  });
  assert.equal(msg.message_id, 'R1');
});

test('reaction removal is a distinct upstream transition', () => {
  const { kind, upstream, msg } = parseEvent({
    type: 'reaction', id: 'R1', chat_jid: '152099@g.us', is_group: true,
    from_me: false, sender_jid: '85290000105@s.whatsapp.net',
    sender_phone: '+85290000105', sender_lid: null, push_name: null,
    target_id: '3EB0F0', target_author: '', emoji: '', removed: true,
    timestamp_ms: 1730300001000,
  });
  assert.equal(kind, 'reaction');
  assert.equal(upstream, 'r:3EB0F0:R1:+85290000105:remove');
  assert.equal(msg.touch.emoji, '');
  assert.equal(msg.touch.removed, true);
});

test('a delete touches its target with an empty body', () => {
  const { kind, upstream, msg } = parseEvent({
    type: 'delete', id: 'D1', chat_jid: '152099@g.us', is_group: true,
    from_me: false, sender_jid: '85290000105@s.whatsapp.net',
    sender_phone: '+85290000105', sender_lid: null, push_name: null,
    target_id: '3EB0F0', target_author: '+85290000111', timestamp_ms: 1730300002000,
  });
  assert.equal(kind, 'delete');
  assert.equal(upstream, 'd:3EB0F0:D1');
  assert.deepEqual(msg.touch, {
    kind: 'delete', target: { message_id: '3EB0F0', sent_ts: null, mentions_known: true },
    target_author: '+85290000111', emoji: '', removed: true,
  });
});

test('add → remove → add are three distinct upstream transitions', () => {
  // Real upstream fields: each reaction stanza carries its own key.id (the
  // Baileys msg.key.id of the reactionMessage), the target lives in
  // reactionMessage.key.id, and removal arrives as an empty reaction text.
  // The transition suffix additionally survives a hypothetical stanza-id
  // reuse across add/remove, which the id alone could not express.
  const base = {
    type: 'reaction', chat_jid: '152099@g.us', is_group: true, from_me: false,
    sender_jid: '85290000105@s.whatsapp.net', sender_phone: '+85290000105',
    target_id: '3EB0F0', target_author: '+85290000111',
  };
  const add1 = parseEvent({ ...base, id: 'R-ADD-1', emoji: '👍', removed: false });
  const rem = parseEvent({ ...base, id: 'R-REM-1', emoji: '', removed: true });
  const add2 = parseEvent({ ...base, id: 'R-ADD-2', emoji: '👍', removed: false });
  const ids = [add1.upstream, rem.upstream, add2.upstream];
  assert.equal(new Set(ids).size, 3);
  assert.match(rem.upstream, /:remove$/);
  assert.match(add1.upstream, /:add$/);
  // Even under stanza-id reuse, add and remove can never collapse.
  const addReuse = parseEvent({ ...base, id: 'SAME', emoji: '👍', removed: false });
  const remReuse = parseEvent({ ...base, id: 'SAME', emoji: '', removed: true });
  assert.notEqual(addReuse.upstream, remReuse.upstream);
});

test('a touch with no stanza id is ambiguous, never an invented identity', () => {
  const out = parseEvent({
    type: 'reaction', id: '', chat_jid: '152099@g.us', is_group: true,
    from_me: false, sender_jid: '85290000105@s.whatsapp.net',
    sender_phone: '+85290000105', target_id: '3EB0F0', emoji: '👍', removed: false,
  });
  assert.equal(out.ambiguous.kind, 'reaction');
  assert.equal(out.ambiguous.targetId, '3EB0F0');
  // A delete with no stanza id is just as unidentifiable.
  const del = parseEvent({
    type: 'delete', id: null, chat_jid: '152099@g.us', is_group: true,
    from_me: false, sender_jid: '85290000105@s.whatsapp.net',
    sender_phone: '+85290000105', target_id: '3EB0F0',
  });
  assert.equal(del.ambiguous.kind, 'delete');
  // An id-less message likewise — every 'm::sender' would collapse.
  const m = parseEvent({ ...MSG, id: '' });
  assert.equal(m.ambiguous.kind, 'message');
});

// -- parse_event: drop rules (identical to the app parser) -------------------------

test('from_me turns drop (R-12 first pass)', () => {
  assert.equal(parseEvent({ ...MSG, from_me: true }), null);
  assert.equal(parseEvent({ type: 'reaction', from_me: true, chat_jid: 'c@g.us', target_id: 'x' }), null);
});

test('broadcast/newsletter feeds drop', () => {
  assert.equal(parseEvent({ ...MSG, chat_jid: 'status@broadcast' }), null);
  assert.equal(parseEvent({ ...MSG, chat_jid: '152099@newsletter' }), null);
});

test('turns with neither text nor media drop', () => {
  assert.equal(parseEvent({ ...MSG, text: null, media: [] }), null);
  assert.equal(parseEvent({ ...MSG, text: '   ', media: [] }), null);
});

test('non-message types drop (heartbeat/dropped are listener-level)', () => {
  assert.equal(parseEvent({ type: 'heartbeat' }), null);
});

// -- helpers integration (target attribution + raw retention) ---------------------

test('reaction target_author resolves the group participant', () => {
  const recent = new Map();
  const rawTarget = {
    key: { remoteJid: '152099@g.us', fromMe: false, id: '3EB0F0', participant: '85290000111@s.whatsapp.net' },
    message: { conversation: 'orig' }, messageTimestamp: 1730299990,
  };
  recent.set('3EB0F0', { raw: rawTarget });
  const event = extractMessageEvent({
    key: { remoteJid: '152099@g.us', fromMe: false, id: 'R1', participant: '85290000105@s.whatsapp.net' },
    message: { reactionMessage: {
      key: rawTarget.key, text: '👍', senderTimestampMs: 1730300000000,
    } },
    messageTimestamp: 1730300000,
  }, { recentCache: recent, lidToPhone: new Map(), phoneToLid: new Map(),
       self: { phone: '999', lid: null } });
  assert.equal(event.type, 'reaction');
  assert.equal(event.target_id, '3EB0F0');
  assert.equal(event.target_author, '+85290000111');
  assert.equal(event.emoji, '👍');
  assert.equal(event.removed, false);
});

test('raw stanzas are retained for quoting and touch attribution', () => {
  const cache = createRecentMessageCache();
  const raw = {
    key: { remoteJid: '85290000100@s.whatsapp.net', fromMe: false, id: 'M1' },
    message: { conversation: 'hi' }, messageTimestamp: 1730300000,
  };
  // remember() is called by the upsert loop on every inbound stanza.
  cache.remember(raw);
  const cached = cache.get('M1');
  assert.ok(cached?.raw === raw);
  assert.deepEqual(cached.key, {
    id: 'M1', remoteJid: '85290000100@s.whatsapp.net', fromMe: false,
  });
});
