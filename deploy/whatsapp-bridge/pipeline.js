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

  // The committed-state ledger for content-derived transition identities:
  // `gu`/`gpu`/`gus`/`gjr`/`puc` callback upstreams and clockless `:na`
  // reactions reuse the FIRST transition's key byte-for-byte, so a genuine
  // A→B→A re-transition would silently dedupe into falsely-current B. Each
  // key holds { seen:Set<state>, last, lastSeq } — the journal (not memory)
  // is the authority; the map is rebuilt from the `transitions` folded into
  // each envelope record on first use after a restart, exactly as the
  // Instagram bridge's capture rebuilds its ledger. Ambiguous verdicts are
  // journaled and never consume a state.
  const transitionLedger = new Map();
  let ledgerReady = false;

  function ensureLedger() {
    if (ledgerReady) return;
    ledgerReady = true;
    for (const r of journal.recover()) {
      if (r && r.envelope && Array.isArray(r.transitions)) {
        for (const t of r.transitions) commitTransition(t);
      }
    }
  }

  function commitTransition(t) {
    const key = typeof t?.key === 'string' ? t.key : '';
    if (!key) return;
    let st = transitionLedger.get(key);
    if (!st) {
      st = { seen: new Set(), last: null, lastSeq: 0 };
      transitionLedger.set(key, st);
    }
    st.seen.add(t.state);
    st.last = t.state;
    const seq = Number(t?.upstream_seq) || 0;
    if (seq > st.lastSeq) st.lastSeq = seq;
  }

  /**
   * emit / replay / ambiguous for one event's transitions — the JS mirror
   * of the Instagram sink's resolveTransitions, same three outcomes:
   *
   *   - emit: a genuinely new transition — forward upstream revision, a
   *     same-revision new state, or a never-before-committed state.
   *   - replay (emit=false, reason=''): an identical redelivery — the
   *     committed state repeated, or a state committed at this revision
   *     or later. Suppressed silently; it IS a replay by definition.
   *   - ambiguous (emit=false, reason set): ordering cannot be grounded —
   *     a state the key already committed under a clockless identity
   *     (stale redelivery and genuine re-transition are indistinguishable),
   *     or a never-committed state below the upstream watermark. Journaled
   *     `ambiguous`, never queued, and the ledger is NOT updated.
   *
   * No sequence numbers are minted: `upstream_seq` is whatever clock the
   * source actually stamped (a reaction/uq timestamp), 0 when it stamped
   * none.
   */
  function resolveTransitions(list) {
    for (const t of list) {
      const st = transitionLedger.get(t.key) || { seen: new Set(), last: null, lastSeq: 0 };
      const seen = st.seen.has(t.state);
      const seq = Number(t.upstream_seq) || 0;
      if (seq > 0) {
        if (seq > st.lastSeq) continue;
        if (seq < st.lastSeq && !seen) {
          return { emit: false, reason: 'stale upstream revision below watermark' };
        }
        if (seen && st.lastSeq > 0 && seq <= st.lastSeq) {
          return { emit: false, reason: '' };
        }
        continue; // same revision, new state — same-batch transition
      }
      if (t.state === st.last) return { emit: false, reason: '' };
      if (seen) {
        return { emit: false, reason: 'unresolvable clockless transition: state repeats without upstream revision' };
      }
    }
    return { emit: true, reason: '' };
  }

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
    const { kind, upstream, msg, observation } = parsed;
    // Routing fields come from the utterance when one exists, else the
    // observation event's own room/actor header — the admission check is
    // identical either way.
    const route = msg ? {
      conversation_id: msg.conversation_id,
      conversation_type: msg.conversation_type,
      sender_id: msg.sender_id,
      sender_name: msg.sender_name,
      sent_ms: msg.timestamp || null,
      message_id: msg.message_id,
    } : parsed.route;
    if (!projection.admitted(route.conversation_id, route.sender_id, route.conversation_type)) {
      inc('bridge_capture_total', { outcome: 'denied' });
      jlog('capture.denied', { conv_type: route.conversation_type, kind });
      return;
    }

    // Transition-identity resolution BEFORE the emit/journal commit: a
    // content-derived upstream that repeats a committed state is decided
    // by the ledger, not by source-key dedupe — a genuine clockless
    // re-transition is retained as ambiguous rather than deduped into a
    // falsely-current last state.
    const declared = Array.isArray(parsed.transitions) ? parsed.transitions : [];
    if (declared.length) {
      ensureLedger();
      const verdict = resolveTransitions(declared);
      if (!verdict.emit) {
        if (!verdict.reason) {
          inc('bridge_capture_total', { outcome: 'transition_replay' });
          return;
        }
        noteAmbiguous({
          conversationId: route.conversation_id,
          conversationType: route.conversation_type,
          senderId: route.sender_id,
          identity: upstream,
          targetId: declared[0]?.key || null,
          reason: verdict.reason,
          kind,
        });
        return;
      }
    }

    // Admitted only: the compat SSE emit IS a capture surface — a denied
    // room's body must reach no consumer at all. Pure observations carry no
    // utterance, so they never feed the legacy (message-shaped) emit.
    if (msg) emitLegacy?.(event);

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
    const mediaDesc = msg && Array.isArray(event.media) ? event.media : [];
    for (let i = 0; msg && i < mediaDesc.length && i < msg.media.length; i++) {
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
    const sentMs = msg ? (msg.timestamp || null) : (route.sent_ms ?? null);
    const timeQuality = msg
      ? (sentMs ? 'upstream' : 'bridge_local')
      : (route.time_quality === 'upstream' ? 'upstream' : 'bridge_local');
    // The observation's received stamp is the same capture instant as the
    // envelope's — the record's one local time, stamped here not in the
    // extractor so a replayed build cannot drift from its envelope.
    const obsOut = observation ? { ...observation, received_ms: now } : null;
    const receiptEmoji = kind === 'message' && msg ? projection.receiptEmoji : null;
    const eligible = receiptEmoji && projection.addressed(msg);
    const env = buildEnvelope({
      platform, accountKey, kind, upstream,
      conversationId: route.conversation_id,
      conversationType: route.conversation_type,
      senderId: route.sender_id,
      senderName: route.sender_name,
      sentMs, receivedMs: now, queuedMs: now,
      timeQuality,
      message: msg,
      observation: obsOut,
      attachments: out,
      trace: captureTp ? { traceparent: captureTp } : {},
      receipt: eligible ? { emoji: receiptEmoji } : null,
      admissionRev: projection.revision,
      correlationKey, keyVersion,
    });
    const react = eligible ? {
      target: { kind: route.conversation_type === 'group' ? 'group' : 'dm', id: route.conversation_id },
      emoji: receiptEmoji,
      to_author: route.sender_id,
      to_timestamp: sentMs || now,
      to_message_id: route.message_id,
      remove: false,
    } : null;

    try {
      // The committed transition states ride the SAME record — no crash
      // window between a transition and the event it belongs to exists,
      // and the ledger rebuild on restart re-folds them in journal order.
      journal.append({
        envelope: env,
        ...(react ? { react } : {}),
        ...(declared.length ? { transitions: declared } : {}),
      });
    } catch (err) {
      captureSpan?.end?.('journal_failed');
      throw halt(err);
    }
    for (const t of declared) commitTransition(t);
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
  function noteAmbiguous({ conversationId, conversationType, senderId, stanzaId, targetId, identity, reason, kind = 'edit' }) {
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
        // The upstream identity whose transition could not be grounded —
        // the diagnosis a replayed ambiguous record re-derives from.
        identity: identity || null,
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
