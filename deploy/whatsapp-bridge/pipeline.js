/**
 * The durable capture pipeline: admission → media custody → envelope →
 * fsync'd journal → (async) ordered drain → Redis enqueue (deduped) →
 * queued receipt. This is the correctness path the SSE/buffer pair used to
 * fake; the legacy emit stays wired alongside for the app's migration, but
 * nothing here depends on it.
 *
 * Ordering is the contract:
 *   1. admission — denied rooms produce NO journal row, no bytes, nothing
 *   2. custody   — admitted media downloads into the spool BEFORE the
 *      envelope is written, so the journaled record already names the keys
 *   3. journal   — append+fsync is the capture's commit point; capture
 *      returns here and NEVER waits on Redis. A journal failure means
 *      capture is broken and upstream must stop (the caller kills the socket)
 *   4. drain     — a single-flight loop replays the journal head-first:
 *      publish order is journal order, so a later event can never overtake
 *      an older journaled head while Redis is down. Each enqueue attempt is
 *      time-bounded so a hung connection cannot stall the loop.
 *   5. receipt   — {emoji} means "queued", sent only after enqueue success
 *      and only for deterministically addressed events, under the ORIGINAL
 *      event alias the stream entry was queued with (survives key rotation)
 */
import { buildEnvelope } from './envelope.js';
import { JournalError } from './journal.js';
import { parseEvent } from './normalize.js';

const ENQUEUE_TIMEOUT_MS = 15000;
const DRAIN_BUDGET = 500;        // journal records per drain pass
const COMPACT_AFTER = 1000;      // records beyond which a drained spool rewrites
const RECEIPT_RETRYABLE = new Set(['unavailable', 'not_connected']);

function withTimeout(promise, ms, label) {
  let timer;
  const bound = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  return Promise.race([promise, bound]).finally(() => clearTimeout(timer));
}

export function createPipeline({
  platform = 'whatsapp',
  accountKey,
  correlationKey,
  keyVersion,
  journal,
  producer,
  projection,
  attachments,
  downloadMedia = null,    // async (rawMsg, descriptor) => Buffer | null
  runReceipt = null,       // async ({ event_id, request }) => {state,...}
  emitLegacy = null,       // (event) => void — compat SSE fan-out
  tracer = null,           // tracing.initTracing() surface (null = no spans)
  metrics = null,
  jlog = () => {},
}) {
  const inc = (name, labels) => metrics?.inc(name, { platform, ...labels });
  // Last attempt span context per `{event_id}:{phase}` — this-process only;
  // a restart loses it and retry links degrade to the capture link, which is
  // the durable lineage.
  const attemptCts = new Map();
  const tpOf = (span) => tracer?.traceparent?.(span?.spanContext?.()) || null;
  const linksFor = (eventId, phase, captureTp) =>
    [captureTp, attemptCts.get(`${eventId}:${phase}`)].filter(Boolean);
  const settled = (eventId) => {
    attemptCts.delete(`${eventId}:enqueue`);
    attemptCts.delete(`${eventId}:receipt`);
  };
  let broken = null;       // JournalError that halted capture
  let drainRunning = false;
  let drainAgain = false;
  let drainPromise = Promise.resolve();

  function halt(err) {
    broken = err;
    jlog('capture.halt', { reason: 'journal_broken' });
    return err;
  }

  /**
   * The durable half of one upserted stanza. `event` is the
   * extractMessageEvent output; `rawMsg` the (possibly decrypt-swapped)
   * Baileys message for media download. Returns once the record is fsync'd;
   * the publish happens on the drain, so a stalled Redis never stalls
   * upstream receive.
   */
  async function capture(event, rawMsg) {
    if (broken) return;
    const parsed = parseEvent(event, { log: (m) => jlog('capture.parse_skip', { detail: m.slice(0, 120) }) });
    if (!parsed) {
      inc('bridge_capture_total', { outcome: 'skipped' });
      return;
    }
    if (parsed.ambiguous) {
      // No stable upstream identity — retained, never queued, never guessed.
      noteAmbiguous(parsed.ambiguous);
      return;
    }
    const { kind, upstream, msg } = parsed;
    if (!projection.admitted(msg.conversation_id, msg.sender_id, msg.conversation_type)) {
      inc('bridge_capture_total', { outcome: 'denied' });
      jlog('capture.denied', { conv_type: msg.conversation_type, kind });
      return;
    }
    // Admitted only: the compat SSE emit IS a capture surface — a denied
    // room's body must reach no consumer at all.
    emitLegacy?.(event);

    // The capture span is the queue producer: started before custody, its
    // exported context becomes env.trace, and it ends right after the fsync —
    // never held across the drain wait. When tracing is off the envelope
    // carries an empty validated trace, not an invented one.
    const captureSpan = tracer?.startSpan('bridge.capture', {
      kind: 'producer', attributes: { 'nora.trace_kind': 'queue' },
    });
    const captureTp = tpOf(captureSpan);

    // -- media custody: download admitted media into the spool --------------
    const out = [];
    const mediaDesc = Array.isArray(event.media) ? event.media : [];
    for (let i = 0; i < mediaDesc.length && i < msg.media.length; i++) {
      const desc = mediaDesc[i];
      const ref = msg.media[i];
      if (desc.size && desc.size > attachments.maxBytes) {
        inc('bridge_media_custody_total', { outcome: 'oversized' });
        continue;
      }
      if (!downloadMedia) break;
      const cspan = tracer?.startSpan('bridge.custody', {
        links: [captureTp].filter(Boolean),
        attributes: { 'nora.trace_kind': 'queue', 'bridge.custody.kind': 'download' },
      });
      let custodyErr = null;
      try {
        const buf = await withTimeout(downloadMedia(rawMsg, desc), 30000, 'media download');
        if (!buf || buf.length === 0) {
          inc('bridge_media_custody_total', { outcome: 'empty' });
          custodyErr = 'custody_empty';
        } else if (buf.length > attachments.maxBytes) {
          inc('bridge_media_custody_total', { outcome: 'oversized' });
          custodyErr = 'custody_oversized';
        } else {
          const { key, size } = attachments.putInbound(buf);
          ref.platform_id = key;
          out.push({ key, media_type: ref.media_type, file_name: null, size });
          cspan?.setAttribute?.('bridge.media.size', size);
          inc('bridge_media_custody_total', { outcome: 'stored' });
        }
      } catch {
        // Custody failed honestly: the ref keeps platform_id '' — the fact
        // of media is recorded, the bytes are marked absent, nothing faked.
        inc('bridge_media_custody_total', { outcome: 'failed' });
        custodyErr = 'custody_failed';
      }
      cspan?.end?.(custodyErr);
    }

    const now = Date.now();
    const sentMs = msg.timestamp || null;
    const receiptEmoji = kind === 'message' ? projection.receiptEmoji : null;
    const eligible = receiptEmoji && projection.addressed(msg);
    const env = buildEnvelope({
      platform, accountKey, kind, upstream,
      conversationId: msg.conversation_id,
      conversationType: msg.conversation_type,
      senderId: msg.sender_id,
      senderName: msg.sender_name,
      sentMs, receivedMs: now, queuedMs: now,
      timeQuality: sentMs ? 'upstream' : 'bridge_local',
      message: msg,
      attachments: out,
      trace: captureTp ? { traceparent: captureTp } : {},
      receipt: eligible ? { emoji: receiptEmoji } : null,
      admissionRev: projection.revision,
      correlationKey, keyVersion,
    });
    const react = eligible ? {
      target: { kind: msg.conversation_type === 'group' ? 'group' : 'dm', id: msg.conversation_id },
      emoji: receiptEmoji,
      to_author: msg.sender_id,
      to_timestamp: sentMs || now,
      to_message_id: msg.message_id,
      remove: false,
    } : null;

    try {
      journal.append(react ? { envelope: env, react } : { envelope: env });
    } catch (err) {
      captureSpan?.end?.('journal_failed');
      throw halt(err);
    }
    captureSpan?.end?.();
    inc('bridge_capture_total', { outcome: 'journaled' });
    pump();
    return env.event_id;
  }

  /**
   * One head of the drain plan: publish the envelope, then run its queued
   * receipt under the original alias. Returns false when the enqueue could
   * not confirm — the caller stops the pass, preserving publish order.
   */
  async function drainItem(item) {
    const eventId = item.envelope.event_id;
    // The capture span is the durable producer lineage; every drain attempt
    // is a fresh root that links back to it (and to the previous attempt of
    // this phase when one ran in this process).
    const captureTp = item.envelope.trace?.traceparent || null;
    let origEventId = item.enqueued?.orig_event_id || null;
    if (!item.enqueued) {
      const pspan = tracer?.startSpan('bridge.queue.publish', {
        kind: 'producer',
        links: linksFor(eventId, 'enqueue', captureTp),
        attributes: { 'nora.trace_kind': 'queue', 'messaging.destination': 'nq:in' },
      });
      attemptCts.set(`${eventId}:enqueue`, tpOf(pspan));
      let res;
      try {
        res = await withTimeout(producer.enqueue(item.envelope), ENQUEUE_TIMEOUT_MS, 'enqueue');
      } catch (err) {
        // Redis-side failure: the journal holds it; the next pump retries.
        inc('bridge_enqueue_total', { outcome: 'failed' });
        pspan?.end?.('enqueue_failed');
        return false;
      }
      pspan?.end?.();
      origEventId = res.eventId;
      try {
        journal.append({ enqueued: {
          event_id: eventId,
          stream_id: res.streamId,
          orig_event_id: res.eventId,
        } });
      } catch (err) {
        throw halt(err);
      }
      inc('bridge_enqueue_total', { outcome: 'enqueued' });
      if (!item.react || !runReceipt) settled(eventId);
    }
    if (item.react && runReceipt) {
      const rspan = tracer?.startSpan('bridge.receipt', {
        kind: 'producer',
        links: linksFor(eventId, 'receipt', captureTp),
        attributes: { 'nora.trace_kind': 'queue' },
      });
      attemptCts.set(`${eventId}:receipt`, tpOf(rspan));
      let res = null;
      let receiptErr = null;
      try {
        res = await runReceipt({
          event_id: origEventId, request: item.react,
          traceLinks: [captureTp, attemptCts.get(`${eventId}:enqueue`)].filter(Boolean),
        });
      } catch (err) {
        receiptErr = 'receipt_failed';
      }
      const state = res?.state || 'unavailable';
      rspan?.setAttribute?.('bridge.action.state', state);
      rspan?.end?.(receiptErr || (state === 'uncertain' ? 'uncertain' : null));
      inc('bridge_receipt_total', {
        outcome: state === 'confirmed' ? 'sent' : 'not_sent',
      });
      if (!RECEIPT_RETRYABLE.has(state)) {
        // Settled — confirmed/rejected/uncertain/started are durable verdicts
        // the action store already owns; the marker closes the journal phase
        // so replay never re-attempts a receipt that ran.
        try {
          journal.append({ receipted: {
            event_id: eventId,
            action_id: `${origEventId}:react:0`,
            state,
          } });
          settled(eventId);
        } catch (err) {
          throw halt(err);
        }
      }
    }
    return true;
  }

  /** One drain pass — bounded, head-first, stops at the first enqueue miss. */
  async function drain() {
    const records = journal.recover();
    const plan = journal.plan(records);
    metrics?.set('bridge_journal_pending', { platform },
      plan.pendingEnqueues + plan.pendingReceipts);
    let done = 0;
    for (const item of plan.work) {
      if (done >= DRAIN_BUDGET) { drainAgain = true; break; }
      if (!(await drainItem(item))) break;
      done += 1;
    }
    if (done > 0 || plan.work.length === 0) {
      const post = journal.plan(journal.recover());
      if (!post.pendingEnqueues && !post.pendingReceipts && post.records > COMPACT_AFTER) {
        journal.compact(journal.compactable(journal.recover()));
      }
    }
  }

  /**
   * Schedule a drain without blocking the caller. Single-flight: a capture
   * landing mid-drain marks `drainAgain` so the loop re-reads the journal —
   * the journal, not memory, is the queue. Await the returned promise when a
   * bound on "published so far" is needed; captures never do.
   */
  function pump() {
    if (drainRunning) { drainAgain = true; return drainPromise; }
    drainRunning = true;
    drainPromise = (async () => {
      do {
        drainAgain = false;
        await drain();
      } while (drainAgain && !broken);
    })().catch((err) => {
      if (err instanceof JournalError) throw err;
      jlog('capture.drain_error', { reason: 'drain_failed' });
      inc('bridge_enqueue_total', { outcome: 'failed' });
    }).finally(() => { drainRunning = false; });
    return drainPromise;
  }

  /**
   * A stanza the bridge could see but not interpret (an undecryptable secret
   * edit, or an upstream transition with no stable identity): retained for
   * diagnosis in admitted rooms, never queued, never guessed.
   */
  function noteAmbiguous({ conversationId, conversationType, senderId, stanzaId, targetId, reason, kind = 'edit' }) {
    if (broken) return;
    if (!projection.admitted(conversationId, senderId, conversationType)) {
      inc('bridge_capture_total', { outcome: 'denied' });
      return;
    }
    try {
      journal.append({ ambiguous: {
        platform, account_key: accountKey, kind,
        conversation_id: conversationId, sender_id: senderId,
        stanza_id: stanzaId || null, target_id: targetId || null,
        reason: String(reason || 'unknown').slice(0, 80),
        received_ms: Date.now(),
      } });
      inc('bridge_capture_total', { outcome: 'ambiguous' });
    } catch (err) {
      throw halt(err);
    }
  }

  /**
   * Await the full drain (boot replay / tests / reconnect pump): every
   * pending publish and owed receipt attempted head-first. A broken journal
   * raises before upstream ever starts.
   */
  async function replay() {
    const records = journal.recover();
    const plan = journal.plan(records);
    jlog('capture.replay', {
      pending_enqueues: plan.pendingEnqueues,
      pending_receipts: plan.pendingReceipts,
    });
    pump();
    await drainPromise;
  }

  return {
    capture, pump, drain, noteAmbiguous, replay,
    get broken() { return broken; },
  };
}
