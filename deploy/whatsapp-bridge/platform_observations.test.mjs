/**
 * Native platform-observation fixture check — CURRENT behavior only.
 *
 * Loads every JSON fixture in testdata/platform-observations/, and for the
 * surfaces today's code path actually touches (messages.upsert stanzas) runs
 * them through extractMessageEvent + parseEvent and asserts the outcome the
 * fixture's own `_fixture.capture_today` records. Unsubscribed-event payloads
 * are validated for shape only: there is no handler to feed them.
 *
 * This pins what IS today (a drop inventory), never what should be: when the
 * binding lane captures a family, its fixture's expectation flips and this
 * test must be updated with it.
 *
 * Run: node --test platform_observations.test.mjs
 */
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractMessageEvent } from './bridge_helpers.js';
import { parseEvent } from './normalize.js';

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'testdata', 'platform-observations');

function loadFixtures() {
  return readdirSync(DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => {
      const doc = JSON.parse(readFileSync(path.join(DIR, f), 'utf8'));
      assert.ok(doc._fixture?.name && doc._fixture.native_event && doc._fixture.capture_today,
        `${f}: _fixture metadata incomplete`);
      return { file: f, doc };
    });
}

// Upsert fixtures: every payload element is a WAMessage-shaped stanza.
const UPSERT = /^upsert-/;
// Stanzas today's extractor turns into events, and the event type it gives.
const CAPTURED = {
  'upsert-message.json': 'message',
  'upsert-reaction-add.json': 'reaction',
  'upsert-reaction-remove-unresolved-target.json': 'reaction',
  'upsert-reaction-add-remove-add.json': 'reaction',
  'upsert-reaction-unknown-actor.json': 'reaction',
};

const fixtures = loadFixtures();
assert.ok(fixtures.length >= 15, 'fixture set unexpectedly small');

test('every upsert payload is a WAMessage shape (key.remoteJid + key.id)', () => {
  for (const { file, doc } of fixtures.filter((f) => UPSERT.test(f.file))) {
    for (const msg of [].concat(doc.payload)) {
      assert.equal(typeof msg.key?.remoteJid, 'string', `${file}: no remoteJid`);
      assert.equal(typeof msg.key?.id, 'string', `${file}: no stanza id`);
    }
  }
});

test('unsubscribed event payloads carry their documented fields', () => {
  const ev = (f) => fixtures.find((x) => x.file === f).doc.payload;
  for (const u of ev('event-groups-update.json')) assert.ok(u.id, 'groups.update entry needs id');
  for (const u of ev('event-group-participants-update.json')) {
    assert.ok(u.id && Array.isArray(u.participants) && typeof u.action === 'string',
      'participants.update needs id/participants/action');
    assert.ok('author' in u, 'participants.update needs the actor field present even when empty');
  }
  const snap = ev('event-groups-upsert-snapshot.json');
  assert.ok(snap[0].participants.length === 4, 'full snapshot carries members');
  assert.ok(!('participants' in snap[1]), 'partial snapshot omits fields, does not null them');
  const hist = ev('event-messaging-history-set.json');
  assert.ok(Array.isArray(hist.messages) && 'isLatest' in hist && 'pastParticipants' in hist);
  const pu = ev('event-messages-update-poll.json');
  assert.ok(pu.update.pollUpdates.length === 2 && pu.update.pollUpdates[1].vote.selectedOptions.length === 0,
    'empty selectedOptions = withdrawn vote, distinct from absent update');
});

test('extractMessageEvent produces today\'s outcome per fixture', () => {
  for (const { file, doc } of fixtures.filter((f) => UPSERT.test(f.file))) {
    const expected = CAPTURED[file];
    for (const msg of [].concat(doc.payload)) {
      const ev = extractMessageEvent(msg, {});
      if (expected) {
        assert.ok(ev, `${file}: expected a ${expected} event, got drop`);
        assert.equal(ev.type, expected, `${file}: event type`);
        const parsed = parseEvent(ev, {});
        assert.ok(parsed, `${file}: parseEvent dropped an extracted event`);
        assert.ok(['message', 'edit', 'reaction', 'delete'].includes(parsed.kind));
      } else {
        assert.equal(ev, null, `${file}: fixture documents a drop; extractor must not have started capturing it`);
      }
    }
  }
});

test('reaction semantics of the captured fixtures', () => {
  const add = fixtures.find((f) => f.file === 'upsert-reaction-add.json').doc.payload;
  assert.equal(extractMessageEvent(add, {}).removed, false);
  const rm = fixtures.find((f) => f.file === 'upsert-reaction-remove-unresolved-target.json').doc.payload;
  const ev = extractMessageEvent(rm, {});
  assert.equal(ev.removed, true);
  const touch = parseEvent(ev, {}).msg.touch;
  assert.equal(touch.target.message_id, '3EB0UNSEEN', 'unresolved target id must survive verbatim');
  const seq = fixtures.find((f) => f.file === 'upsert-reaction-add-remove-add.json').doc.payload;
  const upstreams = seq.map((m) => parseEvent(extractMessageEvent(m, {}), {}).upstream);
  assert.equal(new Set(upstreams).size, 2, 'today: re-add reusing the stanza id collapses into the first add\'s source key');
  assert.match(upstreams[0], /:add$/, '');
  assert.match(upstreams[1], /:remove$/, '');
});
