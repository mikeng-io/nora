// The mint/reuse/discard decisions createCoreRegistry makes (#345): a failed
// join leaves its core in `voice: error` with no session, and service.mjs
// imports discord.js so nothing about it was reachable from a test —
// controller.mjs (createCoreRegistry, createCore and buildCore) has no such
// seam, so everything here runs against fakes or the real wiring, with no
// discord.js in sight.
//
// THE ACTUAL FIX for #345's reported symptom is `discardIfFailed`, called the
// moment a join fails (wired from lifecycle.mjs's join() catch, through
// `buildCore`, into service.mjs's makeCore — and the end-to-end test below
// calls that SAME `buildCore`, not a copy of its wiring). The mint-time
// prune in `controllerFor` is a backstop only: it never runs for a guild
// that fails once and is never joined again, which is exactly the case #345
// reports live — a review of the first version of this fix caught that the
// mint-time prune alone does not touch that case, since re-joining a
// `voice: error` core was already harmless (lifecycle.join treats it like
// idle and retries on the SAME core) and nothing ever forced a re-mint.

import { test } from 'node:test';
import { strict as assert } from 'node:assert';

import { createCoreRegistry, buildCore } from '../src/controller.mjs';

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

// --- discardIfFailed: THE FIX -----------------------------------------------

test('THE FIX: discardIfFailed removes the guild from guildIds immediately — no later join needed', () => {
  const { registry } = makeRegistry({
    cores: [() => fakeCore('g1', { voice: 'error', session: null })],
  });
  const core = registry.mint('g1'); // stands in for lifecycle's join catch having just fired
  assert.deepEqual(registry.guildIds(), ['g1']);

  const removed = registry.discardIfFailed('g1', core);

  assert.equal(removed, true);
  assert.deepEqual(registry.guildIds(), []); // gone from /health with NO retry — the actual #345 symptom
  assert.equal(registry.controllerFor('g1', { create: false }), null);
});

test('discardIfFailed never deletes a DIFFERENT core minted for the same guild in the meantime (identity guard)', () => {
  const { registry, made } = makeRegistry({
    cores: [
      () => fakeCore('g1', { voice: 'error', session: null }),
      () => fakeCore('g1', { voice: 'connected', session: {} }),
    ],
  });
  const failedCore = registry.mint('g1');
  // A concurrent path re-minted for the same guild before the stale join's
  // rejection was handled — `cores.get('g1')` now points at a DIFFERENT core.
  const freshCore = registry.mint('g1');
  assert.notEqual(freshCore, failedCore);

  const removed = registry.discardIfFailed('g1', failedCore); // the STALE core's own callback, arriving late

  assert.equal(removed, false); // refused: not the core the map currently holds
  assert.equal(made.length, 2);
  assert.deepEqual(registry.guildIds(), ['g1']);
  assert.equal(registry.controllerFor('g1', { create: false }), freshCore.controller); // the live one survives untouched
});

test('discardIfFailed refuses a core that is not in voice:error at all', () => {
  const { registry } = makeRegistry({ cores: [() => fakeCore('g1', { voice: 'connected', session: {} })] });
  const core = registry.mint('g1');
  assert.equal(registry.discardIfFailed('g1', core), false);
  assert.deepEqual(registry.guildIds(), ['g1']);
});

// Defensive branch, not a reachable production state today: lifecycle.join's
// catch is the ONLY place `voice` is ever set to 'error', and it does so
// before `session` is ever assigned (lifecycle.mjs) — so a real core can
// never be `voice:error` WITH a session. Kept because discardIfFailed checks
// it anyway rather than trusting the caller, mirroring lifecycle.teardown's
// own `voice === 'error' && !session` guard; this pins that the check is
// really `&&`, not just `voice === 'error'`.
test('discardIfFailed refuses a voice:error core that still holds a session (defensive guard; not reachable via join() today)', () => {
  const { registry } = makeRegistry({ cores: [() => fakeCore('g1', { voice: 'error', session: { fake: true } })] });
  const core = registry.mint('g1');
  assert.equal(registry.discardIfFailed('g1', core), false);
  assert.deepEqual(registry.guildIds(), ['g1']);
});

// --- controllerFor's on-mint prune: the BACKSTOP, not the fix ---------------

test('backstop: a stale voice:error/no-session core is also pruned if someone DOES mint again for that guild', () => {
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

// Same defensive note as discardIfFailed's version above.
test('backstop: an error-state core that still holds a session is not pruned (defensive guard; not reachable via join() today)', () => {
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

// --- other controllerFor behaviour ------------------------------------------

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

// --- end-to-end: the REAL wiring, no fakeCore stand-in ----------------------
//
// Everything above drives createCoreRegistry against a scripted fake core.
// This one wires the actual createLifecycle (via controller.mjs's
// `buildCore`, THE SAME FUNCTION service.mjs's makeCore calls — not a
// second copy of its forward-ref/onJoinFailed wiring) through a real
// createCoreRegistry, with only the Discord/network/process seams faked —
// the same seam pattern test/helpers.mjs uses. A re-review of the first cut
// of this test found it built its own wiring, so a service.mjs regression
// (dropping the forward-ref assignment, or forgetting to pass
// `onJoinFailed`) could not have turned it red; pointing it at `buildCore`
// closes that gap — see `buildCore`'s own comment in controller.mjs.
test('end-to-end: a real join failure removes the guild from /health with no retry, and canonical minting still works', async () => {
  const makeCore = (guildId) =>
    buildCore(registry, guildId, {
      joinVoice: async () => {
        throw new Error('discord unreachable');
      },
      entersStateFn: async () => {},
      stateStore: {
        read: async () => ({ status: 'absent', state: null }),
        write: async () => {},
        path: '/dev/null',
      },
      spawn: () => {
        throw new Error('never called: no successful join in this test');
      },
      createMusicResource: () => {
        throw new Error('never called: no successful join in this test');
      },
      createSpeechResource: () => {
        throw new Error('never called: no successful join in this test');
      },
      fetchImpl: async () => ({ status: 200 }),
      eventUrl: 'http://nora.test/app/v1/voice/event',
      getWakeStats: () => ({}),
      pumpInfo: () => ({}),
      log: () => {},
    });
  const registry = createCoreRegistry({ makeCore, log: () => {} });

  const controller = registry.controllerFor('g-e2e', { create: true });
  assert.notEqual(controller, null);
  assert.deepEqual(registry.guildIds(), ['g-e2e']);

  const [, core] = registry.entries()[0];
  await assert.rejects(core.lifecycle.join('c1'), /discord unreachable/);

  // THE SYMPTOM #345 REPORTS, fixed: gone with no second join attempt.
  assert.deepEqual(registry.guildIds(), []);
  assert.equal(registry.controllerFor('g-e2e', { create: false }), null);

  // And minting still works normally afterward.
  const controller2 = registry.controllerFor('g-e2e', { create: true });
  assert.notEqual(controller2, null);
  assert.deepEqual(registry.guildIds(), ['g-e2e']);
});

// A discarded core must stop its dedupe sweep timer, or every failed join leaves one behind.
test('discardIfFailed disposes the failed core\'s controller', () => {
  let disposed = 0;
  const { registry } = makeRegistry({
    cores: [() => ({
      ...fakeCore('g1', { voice: 'error', session: null }),
      controller: { dispose: () => { disposed += 1; } },
    })],
  });
  const core = registry.mint('g1');
  assert.equal(registry.discardIfFailed('g1', core), true);
  assert.equal(disposed, 1);
});
