// Connection lifecycle (design/voice-channel/07-audio-owner-controller.md
// §3.1): ONE serialized connection lifecycle, single guild hard-bound to
// the configured GUILD_ID:
//
//   idle --join--> connecting --ready--> connected --exit--> destroying --> idle
//   connecting/connected --voice-death--> idle (same teardown, event pushed)
//
// A generation token (uuid) is minted on successful join and invalidated on
// destroy; the controller rejects every state-changing command whose
// generation is stale, which closes the exit-vs-pending-join and
// delayed-play-crosses-exit races. Boot join and HTTP join serialize
// through the same in-flight promise — a join while connecting is not a
// second join. Cutover ordering: the old owner is fully destroyed before
// any new join (the `destroying` state).
//
// Durable desired-state (§12.1): exit writes {desired:idle}, a successful
// join writes {desired:connected, channel_id}. Writes are best-effort — a
// state-file failure must never fail the join itself.

import { randomUUID } from 'node:crypto';
import { httpError } from './errors.mjs';
import { realClock } from './outbox.mjs';

// Boot decision (§12.1): join ONLY if the file says connected, OR the file
// is absent AND env CHANNEL_ID is set (first boot). `idle` wins over env.
export function bootChannel(desired, envChannelId) {
  if (desired?.desired === 'connected') return desired.channel_id ?? envChannelId ?? null;
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
  log = () => {},
  deathRecoveryMs = 15_000,
}) {
  let voice = 'idle'; // idle | connecting | connected | destroying | error
  let generation = null;
  let channelId = null;
  let session = null;
  let joinPromise = null;
  let destroyPromise = null;
  let deathInitiated = false;
  let lastHumanCount = null;

  async function persist(state) {
    try {
      await stateStore.write(state);
    } catch (err) {
      log('error', { event: 'state_store_write_failed', error: String(err?.message ?? err) });
    }
  }

  async function join(targetChannelId) {
    if (voice === 'connected') {
      if (channelId === targetChannelId) return { generation, channelId, already: true };
      throw httpError(409, 'connected_elsewhere');
    }
    if (joinPromise) {
      // A join while connecting is not a second join: ride the in-flight
      // one, then re-check where we landed.
      const landed = await joinPromise;
      if (voice === 'connected' && channelId === targetChannelId) return landed;
      if (voice === 'connected') throw httpError(409, 'connected_elsewhere');
      return landed;
    }
    if (destroyPromise) await destroyPromise; // old owner fully down first

    voice = 'connecting';
    deathInitiated = false;
    lastHumanCount = null;
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
    if (destroyPromise) return destroyPromise;
    if (voice === 'idle' || (voice === 'error' && !session)) return;
    if (joinPromise) await joinPromise.catch(() => {});
    const gen = generation;
    const cid = channelId;
    voice = 'destroying';
    destroyPromise = (async () => {
      try {
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
        generation = null;
        channelId = null;
        voice = 'idle';
        destroyPromise = null;
        if (kind !== 'shutdown') await persist({ desired: 'idle' });
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
    s.client?.on?.('voiceStateUpdate', () => checkChannelEmptied(s, gen, cid));
  }

  function checkChannelEmptied(s, gen, cid) {
    if (generation !== gen) return;
    const channel = s.client?.channels?.cache?.get?.(cid);
    const members = channel?.members;
    if (!members) return;
    const humans = [...members.values()].filter((m) => !m.user?.bot).length;
    // "dropped to zero": only fire on a >0 -> 0 transition, never for the
    // first observation (the bot sitting alone since join is not a drop).
    if (lastHumanCount !== null && lastHumanCount > 0 && humans === 0) {
      emitEvent('channel-emptied', { generation: gen, channel_id: cid });
      log('channel-emptied', { channel_id: cid, generation: gen });
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
  };
}
