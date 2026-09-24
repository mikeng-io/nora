// Connection lifecycle (design/voice-channel/07-audio-owner-controller.md
// §3.1): ONE serialized connection lifecycle, single guild hard-bound to
// the configured GUILD_ID:
//
//   idle --join--> connecting --ready--> connected --exit--> destroying --> idle
//   connecting/connected --voice-death--> idle (same teardown, event pushed)
//
// A generation token (uuid) is minted on successful join and invalidated the
// MOMENT teardown begins; the controller rejects every state-changing
// command whose generation is stale, which closes the exit-vs-pending-join,
// delayed-play-crosses-exit and command-during-teardown races.
//
// Serialization: join and teardown are mutually exclusive through
// joinPromise/destroyPromise, and EVERY wait re-checks the world after it
// resolves — a join that waited for a destroy (or two joins that waited for
// the same destroy) re-runs its entry checks instead of blindly proceeding,
// so exactly one subsequent joinVoice happens.
//
// Intentional teardown suppresses the death watcher: the generation is
// invalidated before the socket is destroyed, so the 'destroyed' stateChange
// a deliberate destroy provokes reads as stale and no voice-death event is
// emitted for an explicit exit.
//
// Durable desired-state (§12.1): exit/death write {desired:idle}, a
// successful join writes {desired:connected, channel_id}. The exit/death
// write happens BEFORE the socket is torn down — a crash anywhere in
// teardown then boots to idle instead of rejoining a channel the user just
// left. Writes are best-effort for the command itself but LOUD: a failure is
// an ERROR log and flips stateWriteOk, surfaced as state_write_ok:false in
// /voice/status.
//
// Empty-channel auto-leave (§12.6): the last human leaving (or Nora joining
// an already-empty channel) arms a 1-minute grace timer instead of exiting
// immediately; any human observed before it fires cancels it. On fire, the
// controller itself emits channel-emptied and tears the connection down
// (teardown('exit'), so desired=idle persists same as any other exit) — this
// is the one case where the controller initiates its OWN teardown rather
// than waiting on an HTTP command. Every other teardown path (explicit exit,
// voice-death) cancels the pending timer first so it can never fire twice or
// touch a connection it was not armed for.

import { randomUUID } from 'node:crypto';
import { httpError } from './errors.mjs';
import { realClock } from './outbox.mjs';

// Boot decision (§12.1): join ONLY if the file says connected, OR the file
// is absent AND env CHANNEL_ID is set (first boot). `idle` wins over env.
// The persisted channel is honored only while it IS the configured one: if
// the operator changed CHANNEL_ID, boot-joining the OLD channel would park
// the controller in a channel /voice/join then refuses (2026-09-16 audit
// C-2) — a mismatch logs an ERROR and the persisted state reads as absent.
export function bootChannel(desired, envChannelId, log = () => {}) {
  if (desired?.desired === 'connected') {
    if (envChannelId && desired.channel_id && desired.channel_id !== envChannelId) {
      log('error', {
        event: 'desired_state_channel_mismatch',
        persisted_channel_id: desired.channel_id,
        env_channel_id: envChannelId,
      });
      return envChannelId;
    }
    return desired.channel_id ?? envChannelId ?? null;
  }
  if (desired?.desired === 'idle') return null;
  if (!desired && envChannelId) return envChannelId;
  return null;
}

export function createLifecycle({
  guildId,
  joinVoice, // async (channelId) => { connection, client, player, pump, leave, bus }
  entersStateFn, // async (connection, status, timeoutMs) — death recovery window
  stateStore,
  clock = realClock,
  emitEvent = () => {},
  onJoined = () => {}, // (session, generation) — wire the arbiter's player
  onTeardown = () => {}, // stop music + speech before the socket dies
  // Fired synchronously, right after `voice` flips to 'error', from the ONE
  // place that ever sets it (#345) — both the boot-restore path and the HTTP
  // /voice/join handler call this same `join()`, so a single hook here covers
  // both without either caller needing its own copy. The registry (the only
  // thing that knows whether THIS core is still the one it holds for the
  // guild — a concurrent second join may already have replaced it) is what
  // actually acts on it; lifecycle only reports the fact.
  onJoinFailed = () => {},
  log = () => {},
  deathRecoveryMs = 15_000,
  emptyChannelGraceMs = 60_000, // §12.6: grace before an empty channel auto-leaves
}) {
  let voice = 'idle'; // idle | connecting | connected | destroying | error
  let generation = null;
  let channelId = null;
  let session = null;
  let joinPromise = null;
  let destroyPromise = null;
  let deathInitiated = false;
  let lastHumanCount = null;
  let emptyChannelTimer = null; // clock.setTimeout handle for §12.6's grace period, or null when unarmed
  let stateWriteOk = true;

  async function persist(state) {
    try {
      await stateStore.write(state);
      stateWriteOk = true;
    } catch (err) {
      stateWriteOk = false;
      log('error', { event: 'state_store_write_failed', error: String(err?.message ?? err) });
    }
  }

  async function join(targetChannelId) {
    for (;;) {
      if (voice === 'connected') {
        if (channelId === targetChannelId) {
          // Re-assert the arbiter's player on the already-connected path.
          // `onJoined` is the ONLY caller of `arbiter.attach`, so a
          // connection that survived here while the arbiter detached (voice
          // death handled out of order, a teardown that lost the race) left
          // `voice === 'connected'` with a null player — and every play
          // after that was dropped in silence. `attach` is a no-op when the
          // player is already the current one, so this costs nothing when
          // nothing is wrong.
          // Guarded: this path previously returned 200 `already:true` with no
          // arbiter call at all, so a null session must not turn it into a 500.
          if (session?.player) onJoined(session, generation);
          return { generation, channelId, already: true };
        }
        throw httpError(409, 'connected_elsewhere');
      }
      if (joinPromise) {
        // A join while connecting is not a second join: ride the in-flight
        // one; its failure is every waiter's failure.
        const err = await joinPromise.then(
          () => null,
          (e) => e,
        );
        if (err) throw err;
        continue; // landed somewhere — re-run the entry checks
      }
      if (destroyPromise) {
        await destroyPromise; // old owner fully down first
        continue; // another join may have won the race meanwhile — re-check
      }
      break;
    }

    voice = 'connecting';
    deathInitiated = false;
    lastHumanCount = null;
    // A fresh join starts with no grace timer of its own; any timer from the
    // connection being replaced should already be gone via teardown()'s own
    // clear, but a new generation must never inherit a stale one regardless.
    clearEmptyChannelTimer();
    joinPromise = (async () => {
      try {
        const s = await joinVoice(targetChannelId);
        session = s;
        generation = randomUUID();
        channelId = targetChannelId;
        voice = 'connected';
        watchConnection(s, generation, targetChannelId);
        onJoined(s, generation);
        await persist({ desired: 'connected', channel_id: targetChannelId });
        log('join', { channel_id: targetChannelId, generation });
        return { generation, channelId: targetChannelId, already: false };
      } catch (err) {
        voice = 'error';
        log('error', { event: 'join_failed', error: String(err?.message ?? err) });
        // `session` was never assigned on this path — the registry's own
        // check confirms that before it acts, but the fact is decided HERE.
        onJoinFailed();
        throw httpError(503, 'join_failed', String(err?.message ?? err));
      } finally {
        joinPromise = null;
      }
    })();
    return joinPromise;
  }

  // kind: 'exit' (explicit, persist idle) | 'death' (persist idle + event
  // already pushed) | 'shutdown' (process going down — do NOT change the
  // desired state; a restart should come back to what was desired).
  async function teardown(kind) {
    // Every teardown path — explicit exit, death, or the empty-channel timer
    // firing itself — makes any pending §12.6 grace timer moot: once the
    // connection is going down there is nothing left for it to time out.
    // Clearing unconditionally (even on the early returns below) means an
    // already-idle or already-different-generation connection can never be
    // re-tumbled by a timer that outlived the session it was armed for.
    clearEmptyChannelTimer();
    if (destroyPromise) return destroyPromise;
    if (voice === 'idle' || (voice === 'error' && !session)) return;
    if (joinPromise) {
      await joinPromise.catch(() => {});
      // Re-check after the wait: another teardown may have started (or
      // finished) while the join settled.
      if (destroyPromise) return destroyPromise;
      if (voice === 'idle' || (voice === 'error' && !session)) return;
    }
    const gen = generation;
    const cid = channelId;
    voice = 'destroying';
    // Invalidate FIRST: no command passes the generation guard from here on,
    // and the death watcher reads as stale for the destroy we are about to
    // perform (no voice-death for an intentional exit).
    generation = null;
    destroyPromise = (async () => {
      try {
        // Persist exit intent BEFORE the socket dies: a crash anywhere below
        // boots to idle, never back into a channel the user just left.
        if (kind !== 'shutdown') await persist({ desired: 'idle' });
        onTeardown(); // arbiter.detach(): music + speech stop, no events
        const s = session;
        session = null;
        if (s?.pump) {
          try {
            s.pump.close();
          } catch {
            /* already closed */
          }
        }
        if (s?.leave) {
          try {
            await s.leave();
          } catch {
            /* half-open socket */
          }
        } else {
          try {
            s?.connection?.destroy();
          } catch {
            /* already destroyed */
          }
          try {
            s?.client?.destroy();
          } catch {
            /* already destroyed */
          }
        }
      } finally {
        channelId = null;
        voice = 'idle';
        destroyPromise = null;
        log(kind === 'death' ? 'voice-death' : 'exit', { channel_id: cid, generation: gen });
      }
    })();
    return destroyPromise;
  }

  async function handleDeath(gen, cid, why) {
    if (generation !== gen || deathInitiated) return;
    deathInitiated = true;
    log('voice-death', { channel_id: cid, generation: gen, reason: why });
    emitEvent('voice-death', { generation: gen });
    await teardown('death');
  }

  function humanCount(s, cid) {
    const channel = s.client?.channels?.cache?.get?.(cid);
    const members = channel?.members;
    if (!members) return null;
    return [...members.values()].filter((m) => !m.user?.bot).length;
  }

  // §12.6: an empty channel does not exit immediately — it starts a 1-minute
  // grace timer, cancelled the moment a human is next observed. clearing is
  // idempotent (clock.clearTimeout on an already-fired/absent handle is a
  // no-op) so every call site can clear defensively without checking first.
  function clearEmptyChannelTimer() {
    if (emptyChannelTimer !== null) {
      clock.clearTimeout(emptyChannelTimer);
      emptyChannelTimer = null;
    }
  }

  // gen/cid are captured at arm time, the same pattern handleDeath/teardown
  // use for their own async callbacks: a timer armed for one generation must
  // never act on whatever connection happens to be live when it fires.
  function armEmptyChannelTimer(gen, cid) {
    clearEmptyChannelTimer(); // never let two timers race for the same drop
    emptyChannelTimer = clock.setTimeout(() => {
      emptyChannelTimer = null;
      void fireEmptyChannelTimeout(gen, cid);
    }, emptyChannelGraceMs);
  }

  // The grace period elapsed with nobody returning: emit channel-emptied for
  // nora (mirroring handleDeath's explicit emitEvent-then-teardown shape —
  // teardown()'s own `log()` call is diagnostic only, not the nora-facing
  // event) and then actually tear the connection down ourselves. Unlike the
  // old immediate-fire behavior, nothing else calls exit() on our behalf here
  // — the controller now owns both the "when" and the "who tears it down".
  async function fireEmptyChannelTimeout(gen, cid) {
    if (generation !== gen) return; // stale timer from a dead/replaced connection
    log('channel-emptied', { channel_id: cid, generation: gen });
    emitEvent('channel-emptied', { generation: gen, channel_id: cid });
    await teardown('exit');
  }

  function watchConnection(s, gen, cid) {
    s.connection?.on?.('stateChange', (_oldState, newState) => {
      if (generation !== gen) return; // stale watcher from a dead connection
      const status = newState?.status;
      if (status === 'destroyed') return void handleDeath(gen, cid, 'destroyed');
      if (status === 'disconnected') {
        // @discordjs/voice attempts its own recovery from Disconnected;
        // give it one window before declaring death.
        Promise.resolve(entersStateFn(s.connection, 'ready', deathRecoveryMs)).catch(() =>
          handleDeath(gen, cid, 'disconnected'),
        );
      }
    });
    // Seed membership BEFORE watching changes: a human already present when
    // Nora joins whose departure is the FIRST voice-state update must still
    // read as a >0 -> 0 transition (2026-09-16 audit CN-3).
    lastHumanCount = humanCount(s, cid);
    // §12.6: "alone from join" now carries the SAME policy as a later drop —
    // joining an already-empty channel arms the grace timer right here
    // instead of the old behavior of silently never firing for this case.
    if (lastHumanCount === 0) armEmptyChannelTimer(gen, cid);
    s.client?.on?.('voiceStateUpdate', () => checkChannelEmptied(s, gen, cid));
  }

  function checkChannelEmptied(s, gen, cid) {
    if (generation !== gen) return;
    const humans = humanCount(s, cid);
    if (humans === null) return;
    // "dropped to zero": only ARM the grace timer on a >0 -> 0 transition,
    // never for the first observation (that's the seed, handled above) and
    // never redundantly for a 0 -> 0 update (the timer already armed by the
    // original transition keeps running unmolested).
    if (lastHumanCount !== null && lastHumanCount > 0 && humans === 0) {
      armEmptyChannelTimer(gen, cid);
    } else if (humans > 0) {
      // A human is present again — the room is no longer empty, so any
      // grace timer counting down toward auto-leave is now moot. Cancel it
      // rather than letting it re-arm redundantly on the next drop; the next
      // >0 -> 0 transition arms a fresh one.
      clearEmptyChannelTimer();
    }
    lastHumanCount = humans;
  }

  return {
    join,
    exit: () => teardown('exit'),
    shutdown: () => teardown('shutdown'),
    voice: () => voice,
    generation: () => generation,
    channelId: () => channelId,
    session: () => session,
    guildId: () => guildId,
    stateWriteOk: () => stateWriteOk,
  };
}
