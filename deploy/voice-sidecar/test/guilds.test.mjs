// One process, several guilds: the front door routes an operation to its
// guild's controller, or refuses it.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { createFrontDoor } from '../src/controller.mjs';
import { makeHarness, GUILD, TOKEN, AUTH } from './helpers.mjs';

const OTHER_GUILD = '222222222222222222';

test('an operation for a guild_id that is not a snowflake is refused, and mints nothing', async () => {
  // THE BOUND THIS GUARDS: cores are minted on JOIN only. Minting on any
  // operation would make an unbounded map keyed by whatever a caller puts in
  // guild_id.
  const h = await makeHarness();
  const res = await h.op('stop', { guild_id: 'a-guild-with-no-core', generation: 'x' });
  assert.equal(res.status, 400);
  assert.equal(res.error, 'wrong_guild');
  await h.close();
});

test('an operation with no guild_id at all is refused before any handler runs', async () => {
  const h = await makeHarness();
  const res = await h.op('stop', { generation: 'x' });
  assert.equal(res.status, 400);
  assert.equal(res.error, 'wrong_guild');
  await h.close();
});

test('the status query requires a guild and finds nothing for one with no connection', async () => {
  // The guild rides the query's params. Defaulting to "the only guild" would
  // report one server's playback as another's the moment a second connected.
  const h = await makeHarness();

  const missing = await h.post('/v1/voice/queries', { kind: 'status', params: {} });
  assert.equal(missing.status, 400);
  assert.deepEqual(Object.keys(missing.body), ['error']);

  const unknown = await h.status(OTHER_GUILD);
  assert.equal(unknown.status, 200);
  assert.equal(unknown.outcome, 'nothing_found');
  await h.close();
});

test('a refusal before a guild resolves carries no envelope', async () => {
  // A 400 never became an operation: the body is the refusal alone.
  const h = await makeHarness();
  const res = await h.op('stop', { guild_id: 'nope' });
  assert.deepEqual(res.body, { error: 'wrong_guild' });
  await h.close();
});

test('the front door asks to CREATE only on a join', async () => {
  // THE GAP THIS CLOSES: `makeHarness`'s `controllerFor` ignores the `create`
  // flag entirely, so "an operation for an unknown guild mints nothing" would
  // assert the harness's behaviour, not production's.
  const asked = [];
  const stub = { execute: async () => ({ outcome: 'complete', state: null }) };
  const server = createFrontDoor({
    authToken: TOKEN,
    controllerFor: (guildId, opts = {}) => {
      asked.push({ guildId, create: Boolean(opts.create) });
      return stub;
    },
    health: () => ({ status: 'ok' }),
    log: () => {},
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const send = (kind) => fetch(`${base}/v1/voice/operations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...AUTH },
    body: JSON.stringify({ operation_id: `op-${kind}`, kind, effect: { guild_id: GUILD } }),
  });

  await send('join');
  await send('stop');
  await send('speak');

  assert.deepEqual(asked, [
    { guildId: GUILD, create: true },   // join may create
    { guildId: GUILD, create: false },  // every other kind may not
    { guildId: GUILD, create: false },
  ]);
  await new Promise((r) => server.close(r));
});

test('a COLD BOOT with zero cores still accepts a join', async () => {
  // THE DEADLOCK THIS GUARDS: the front door once asked a CORE whether a route
  // was known. With no cores yet every route 404'd, including the join that
  // would have created the first core. Route knowledge is static.
  const cores = new Map();
  const stub = { execute: async () => ({ outcome: 'complete', state: null }) };
  const server = createFrontDoor({
    authToken: TOKEN,
    controllerFor: (guildId, opts = {}) => {
      if (cores.has(guildId)) return stub;
      if (!opts.create) return null;
      cores.set(guildId, stub);
      return stub;
    },
    health: () => ({ status: 'ok', guilds: [...cores.keys()] }),
    log: () => {},
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;

  assert.equal(cores.size, 0, 'precondition: no cores, as on a cold boot');
  const res = await fetch(`${base}/v1/voice/operations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...AUTH },
    body: JSON.stringify({ operation_id: 'op-cold', kind: 'join', effect: { guild_id: '100000000000000203', channel_id: 'c1' } }),
  });

  assert.equal(res.status, 200, 'a join on a cold boot must not 404');
  assert.equal((await res.json()).outcome, 'complete');
  assert.equal(cores.size, 1);
  await new Promise((r) => server.close(r));
});

test('a guild_id that is not a snowflake is refused before it can name a file', async () => {
  // `guild_id` reaches `path.join(STATE_DIR, `${guildId}.json`)` and mints a core.
  const h = await makeHarness();
  for (const bad of ['../../etc/evil', 'desired_state', '', 'g1', '12345']) {
    const res = await h.op('join', { guild_id: bad, channel_id: 'c1' });
    assert.equal(res.status, 400, bad);
    assert.equal(res.error, 'wrong_guild', bad);
  }
  await h.close();
});

test('a guild the process holds no connection for is rejected unknown_guild, not wrong_guild', async () => {
  // Same fact as the status query's nothing_found: "I have no connection for
  // this guild" is the ordinary state after a restart into idle, and nora
  // reads it as "reconnect me". `wrong_guild` reads as "you are misconfigured".
  const h = await makeHarness();
  const res = await h.op('stop', { guild_id: OTHER_GUILD, generation: 'g' }, { operationId: 'r' });
  assert.equal(res.outcome, 'rejected');
  assert.equal(res.error, 'unknown_guild');
  await h.close();
});
