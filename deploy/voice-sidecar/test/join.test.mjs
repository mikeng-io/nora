// recv.js join() failure-path tests (2026-09-16 audit CN-2): a join that
// fails at any construction step must destroy the client and connection it
// already built, so a retried join never shares the process with zombies.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { EventEmitter } from 'node:events';

import { join } from '../src/recv.js';

function fakeClient() {
  const client = new EventEmitter();
  client.login = async () => 'token';
  client.isReady = () => true;
  client.guilds = {
    cache: new Map([['g1', { voiceAdapterCreator: {} }]]),
    fetch: async () => ({ voiceAdapterCreator: {} }),
  };
  client.destroyed = 0;
  client.destroy = () => {
    client.destroyed += 1;
  };
  return client;
}

function fakeConnection() {
  const connection = new EventEmitter();
  connection.receiver = { speaking: new EventEmitter() };
  connection.destroyed = 0;
  connection.destroy = () => {
    connection.destroyed += 1;
  };
  return connection;
}

test('a voice-connection readiness timeout destroys BOTH the connection and the client', async () => {
  const client = fakeClient();
  const connection = fakeConnection();
  await assert.rejects(
    join('g1', 'c1', 'tok', {
      createClient: () => client,
      joinVoiceChannelImpl: () => connection,
      entersStateImpl: async () => {
        throw new Error('The join was aborted (timeout)');
      },
    }),
    /timeout/,
  );
  assert.equal(connection.destroyed, 1);
  assert.equal(client.destroyed, 1);
});

test('a login failure destroys the client (there is no connection yet)', async () => {
  const client = fakeClient();
  client.login = async () => {
    throw new Error('invalid token');
  };
  await assert.rejects(join('g1', 'c1', 'tok', { createClient: () => client }), /invalid token/);
  assert.equal(client.destroyed, 1);
});

test('a successful join exposes bus/leave/connection/client and leave() destroys both', async () => {
  const client = fakeClient();
  const connection = fakeConnection();
  const session = await join('g1', 'c1', 'tok', {
    createClient: () => client,
    joinVoiceChannelImpl: () => connection,
    entersStateImpl: async () => {},
  });
  assert.equal(session.connection, connection);
  assert.ok(session.bus instanceof EventEmitter);
  await session.leave();
  assert.equal(connection.destroyed, 1);
  assert.equal(client.destroyed, 1);
});
