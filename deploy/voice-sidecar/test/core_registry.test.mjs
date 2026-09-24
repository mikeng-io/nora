// The mint/reuse/prune decision `controllerFor` makes on every request (#345):
// a failed join leaves its core in `voice: error` with no session, and
// service.mjs imports discord.js so nothing about it was reachable from a
// test — createCoreRegistry (controller.mjs) has no such seam, so these run
// against fakes with no discord.js in sight.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { createCoreRegistry } from '../src/controller.mjs';

function fakeCore(guildId, { voice = 'connected', session = {} } = {}) {
  return {
    guildId,
    lifecycle: { voice: () => voice, session: () => session },
    controller: `controller-${guildId}-${voice}-${Math.random().toString(36).slice(2, 8)}`,
  };
}

// `cores` is a queue: each `makeCore` call pops the next fake (or builds a
// default connected one when the queue is empty), so a test can script what
// each successive mint returns.
function makeRegistry({ cores = [], canCreate = () => true } = {}) {
  const queue = [...cores];
  const made = [];
  const registry = createCoreRegistry({
    makeCore: (guildId) => {
      const build = queue.shift() ?? (() => fakeCore(guildId));
      const core = build(guildId);
      made.push(core);
      return core;
    },
    canCreate,
    log: () => {},
  });
  return { registry, made };
}

test('a fresh join mints a core and registers it for /health', () => {
  const { registry, made } = makeRegistry();
  const controller = registry.controllerFor('g1', { create: true });
  assert.equal(controller, made[0].controller);
  assert.deepEqual(registry.guildIds(), ['g1']);
});

test('no core yet and create:false never mints (status/other verbs 404 instead)', () => {
  const { registry, made } = makeRegistry();
  const controller = registry.controllerFor('g1', { create: false });
  assert.equal(controller, null);
  assert.equal(made.length, 0);
});

test('a connected core is reused across repeated joins and other verbs, never re-minted', () => {
  const { registry, made } = makeRegistry({
    cores: [() => fakeCore('g1', { voice: 'connected', session: {} })],
  });
  const first = registry.controllerFor('g1', { create: true });
  const second = registry.controllerFor('g1', { create: true });
  const third = registry.controllerFor('g1', { create: false });
  assert.equal(second, first);
  assert.equal(third, first);
  assert.equal(made.length, 1);
});

// THE FIX: without pruning, this errored core would be handed back forever —
// exactly the bug reported live: /health's `guilds` naming a guild that was
// never actually connected.
test('a core whose join failed (voice:error, no session) is pruned and re-minted on the next join', () => {
  const { registry, made } = makeRegistry({
    cores: [
      () => fakeCore('g1', { voice: 'error', session: null }),
      () => fakeCore('g1', { voice: 'connected', session: {} }),
    ],
  });
  const stale = registry.controllerFor('g1', { create: true });
  assert.equal(stale, made[0].controller);

  const fresh = registry.controllerFor('g1', { create: true });
  assert.notEqual(fresh, stale); // NOT the same errored core handed back again
  assert.equal(fresh, made[1].controller);
  assert.equal(made.length, 2);
  assert.deepEqual(registry.guildIds(), ['g1']); // exactly one core survives, not two
});

test('a stale error core is NOT pruned by a status read (create:false) — it still answers what it knows', () => {
  const { registry, made } = makeRegistry({
    cores: [() => fakeCore('g1', { voice: 'error', session: null })],
  });
  const first = registry.controllerFor('g1', { create: true });
  const readback = registry.controllerFor('g1', { create: false });
  assert.equal(readback, first);
  assert.equal(made.length, 1);
});

test('an error-state core that still holds a session is not pruned (mirrors teardown\'s own guard)', () => {
  const { registry, made } = makeRegistry({
    cores: [
      () => fakeCore('g1', { voice: 'error', session: { fake: true } }),
      () => fakeCore('g1', { voice: 'connected', session: {} }),
    ],
  });
  const first = registry.controllerFor('g1', { create: true });
  const second = registry.controllerFor('g1', { create: true });
  assert.equal(second, first); // reused, not re-minted
  assert.equal(made.length, 1);
});

test('canCreate:false (no token) blocks minting but a reusable core is unaffected', () => {
  const { registry: noToken, made: madeNoToken } = makeRegistry({ canCreate: () => false });
  assert.equal(noToken.controllerFor('g1', { create: true }), null);
  assert.equal(madeNoToken.length, 0);

  const { registry: withCore } = makeRegistry({
    canCreate: () => false,
    cores: [() => fakeCore('g1', { voice: 'connected', session: {} })],
  });
  // mint() (used directly, e.g. at boot) bypasses canCreate by design — but
  // once a core exists, controllerFor reuses it regardless of canCreate.
  withCore.mint('g1');
  assert.notEqual(withCore.controllerFor('g1', { create: true }), null);
});

test('any:true returns the first controller once one exists, independent of the guild id asked', () => {
  const { registry } = makeRegistry({ cores: [() => fakeCore('g1')] });
  assert.equal(registry.controllerFor('anything', { any: true }), null);
  registry.controllerFor('g1', { create: true });
  assert.notEqual(registry.controllerFor('whatever-else', { any: true }), null);
});

test('mint() registers directly (the boot-restore seam), independent of controllerFor', () => {
  const { registry, made } = makeRegistry({ cores: [() => fakeCore('g1', { voice: 'connecting', session: null })] });
  const core = registry.mint('g1');
  assert.equal(core, made[0]);
  assert.deepEqual(registry.guildIds(), ['g1']);
  assert.deepEqual(registry.values(), [core]);
});
