/**
 * `bridge_action` / `bridge_work` access — the JS mirror of
 * `nora/infra/sql/bridge_store.py`'s bridge-owned write surface (schema is
 * migration 0141's, owned by the app; the bridge writes reservations and
 * results, the app reads them).
 *
 * Reservation semantics, identical to `reserve_action`:
 *   fresh id, or same request_hash still 'prepared'   → 'execute'
 *   same request_hash in a later state                → 'replay' (recorded result)
 *   same id, different request OR different immutable  → 'conflict'
 *   context (platform/account/op/owner/generation)       (never executed)
 *
 * The classify-and-mark-'started' step runs under `SELECT … FOR UPDATE` in one
 * transaction, so two concurrent POSTs of one action_id cannot both execute —
 * the second sees `started` and replays the recorded state. `started` is the
 * "the upstream call may have happened" state: it is never retried, matching
 * the wire's no-blind-retry rule.
 *
 * Owner authorization is part of the same transaction for EVERY owner kind —
 * the JS port of `nora/platform/bridge/actions.py::validate_owner`:
 *   - `work`: `w-N` binds to bridge_work row N under its own row lock and
 *     must agree on state ('running'), a LIVE lease on the DB clock,
 *     generation, platform, account and target conversation — a stale,
 *     re-parked or lease-dead owner is rejected durably ('fenced').
 *   - `event_receipt`: the owner names an event THIS bridge's journal holds
 *     with a frozen receipt intent (canonical alias or digest), and no
 *     terminal `bridge_event` row later revoked it.
 *   - `otp_challenge`: a real, unexpired `otp_challenge` row for this
 *     platform plus the committed pending outbound `messages` row tagged
 *     `otp-{id}` — its id becomes `bridge_action.message_id`.
 *   - `live_invocation`: the owner names the pending outbound `messages`
 *     row itself, scoped to platform + conversation.
 * The stored request redacts an OTP send's `text` (a live credential that
 * never rests); `request_hash` is still over the FULL request so two codes
 * fence each other.
 */
import { createHash } from 'node:crypto';
import { canonicalJson } from './canon.js';
import { eventId } from './envelope.js';

export const OWNER_KINDS = new Set(['work', 'event_receipt', 'otp_challenge', 'live_invocation']);
export const ACTION_OPS = new Set(['send', 'react', 'edit', 'typing']);
const TERMINAL = new Set(['confirmed', 'rejected', 'uncertain']);
const OTP_REDACTED = '[redacted]';
const UUID_SHAPE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

export function requestHash(request) {
  return createHash('sha256').update(canonicalJson(request), 'utf8').digest('hex');
}

/** What `bridge_action.request` persists — the FULL request still rides the
 * wire and the hash; only an `otp_challenge` send's `text` is a credential
 * that must not rest (Python's `stored_request`). */
function storedRequest(owner, request) {
  if (owner?.kind === 'otp_challenge' && request && 'text' in request) {
    return { ...request, text: OTP_REDACTED };
  }
  return request;
}

/** The durable evidence an `event_receipt` owner must name: every id under
 * which a journaled envelope's FROZEN receipt intent can be referenced —
 * the journaled `event_id` AND the canonical alias the producer computes
 * (`eventId(source_key, ...)`, rotation-stable). A journal record without
 * `react` carries no intent. */
export function receiptIntentIds(journal, { correlationKey, keyVersion } = {}) {
  const ids = new Set();
  for (const r of journal.recover()) {
    const env = r && r.envelope;
    if (!env || !r.react || typeof env.event_id !== 'string') continue;
    ids.add(env.event_id);
    if (correlationKey && typeof env.source_key === 'string') {
      try { ids.add(eventId(env.source_key, correlationKey, keyVersion)); }
      catch { /* an unreadable source key yields no alias */ }
    }
  }
  return ids;
}

/** `w-7` and bare `7` both name bridge_work.id = 7; anything else binds nothing. */
export function parseWorkId(ownerId) {
  const s = String(ownerId ?? '');
  const m = /^w-(\d+)$/.exec(s);
  if (m) return m[1];
  return /^\d+$/.test(s) ? s : null;
}

/** The wire's ActionResponse derived from a stored row (or a verdict). */
export function actionResponse(row) {
  const state = row.state;
  let outcome = null;
  if (state === 'confirmed' || state === 'uncertain') outcome = state;
  else if (state === 'rejected') outcome = row.error?.kind === 'unsupported' ? 'unsupported' : 'rejected';
  return {
    action_id: row.action_id,
    state,
    outcome,
    platform_ref: row.platform_ref ?? null,
    platform_ms: row.platform_ms ?? null,
    message_id: row.message_id ?? null,
    error: row.error ?? null,
  };
}

export class ActionStore {
  /** `pool` is a `pg.Pool` (or a test double with `.connect()`).
   * `receiptIntents` (optional, may be async) returns the journal-derived
   * set of event ids/digests whose envelopes carry a frozen receipt — the
   * producer evidence an `event_receipt` owner must name. Absent the
   * callable, every `event_receipt` owner denies: never fabricated. */
  constructor(pool, { platform, accountKey, receiptIntents = null }) {
    this.pool = pool;
    this.platform = platform;
    this.accountKey = accountKey;
    this.receiptIntents = receiptIntents;
  }

  /**
   * Reserve `action_id` for `request` under `owner`. Returns
   *   {verdict:'execute'}                — caller runs the op, then finish()
   *   {verdict:'replay', row}            — return the recorded row verbatim
   *                                        (includes a row just rejected by
   *                                        the in-tx owner fence)
   *   {verdict:'conflict', row}          — id spent on a different request or
   *                                        immutable context
   * `execute` leaves the row 'started' so a later replay never double-fires.
   */
  async reserve({ actionId, owner, op, request }) {
    const hash = requestHash(request);
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      await client.query(
        `INSERT INTO bridge_action
           (action_id, platform, account_key, owner_kind, owner_id,
            owner_generation, op, request, request_hash)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
         ON CONFLICT (action_id) DO NOTHING`,
        [actionId, this.platform, this.accountKey, owner.kind, owner.id,
          owner.generation ?? null, op, JSON.stringify(storedRequest(owner, request)), hash],
      );
      const sel = await client.query(
        'SELECT * FROM bridge_action WHERE action_id = $1 FOR UPDATE', [actionId]);
      const row = sel.rows[0];
      const sameContext = row.platform === this.platform
        && row.account_key === this.accountKey
        && row.op === op
        && row.owner_kind === owner.kind
        && row.owner_id === owner.id
        && (row.owner_generation ?? null) === (owner.generation ?? null);
      if (row.request_hash !== hash || !sameContext) {
        await client.query('COMMIT');
        return { verdict: 'conflict', row };
      }
      if (row.state !== 'prepared') {
        await client.query('COMMIT');
        return { verdict: 'replay', row };
      }
      // The closed typed owner gate — EVERY kind, inside the reservation tx.
      const ownerVerdict = await this.validateOwner(client, owner, request);
      if (!ownerVerdict.ok) {
        await client.query(
          `UPDATE bridge_action
             SET state='rejected', error=$2,
                 started_at=COALESCE(started_at, now()), completed_at=now()
           WHERE action_id = $1`,
          [actionId, JSON.stringify({ kind: 'fenced', reason: ownerVerdict.reason })]);
        const fin = (await client.query(
          'SELECT * FROM bridge_action WHERE action_id = $1', [actionId])).rows[0];
        await client.query('COMMIT');
        return { verdict: 'replay', row: fin };
      }
      await client.query(
        `UPDATE bridge_action SET state='started', started_at=now(), message_id=$2
         WHERE action_id = $1`, [actionId, ownerVerdict.messageId ?? null]);
      await client.query('COMMIT');
      return { verdict: 'execute' };
    } catch (err) {
      try { await client.query('ROLLBACK'); } catch { /* already closed */ }
      throw err;
    } finally {
      client.release();
    }
  }

  /**
   * The closed typed authority check — the JS port of
   * `nora/platform/bridge/actions.py::validate_owner`, run under the
   * reservation's own transaction. `{ok:true, messageId}` or
   * `{ok:false, reason}` — a store failure THROWS so the route can answer
   * unavailable, never a silent deny.
   */
  async validateOwner(client, owner, request) {
    const target = request?.target?.id ?? null;
    if (owner.kind === 'work') {
      const workId = parseWorkId(owner.id);
      if (workId == null) return { ok: false, reason: 'malformed_work_id' };
      // Lock the work row: a concurrent claim's generation bump serializes
      // against this check.
      const wrow = (await client.query(
        `SELECT state, generation, platform, account_key, conversation_id,
                payload, (leased_until > now()) AS lease_live
         FROM bridge_work WHERE id = $1 FOR UPDATE`, [workId])).rows[0];
      if (!wrow) return { ok: false, reason: 'unknown_work' };
      if (wrow.platform !== this.platform || wrow.account_key !== this.accountKey) {
        return { ok: false, reason: 'platform_account_mismatch' };
      }
      if (typeof target !== 'string' || wrow.conversation_id !== target) {
        return { ok: false, reason: 'conversation_mismatch' };
      }
      if (wrow.state !== 'running') {
        return { ok: false, reason: 'work_not_running' };
      }
      if (wrow.generation !== owner.generation) {
        return { ok: false, reason: 'stale_generation' };
      }
      // The claim fence's second half: `generation` says WHICH claim, the
      // DB-clock lease says it is still ALIVE — a lease that lapsed before
      // the sweep ran is still a dead owner.
      if (wrow.lease_live !== true) {
        return { ok: false, reason: 'lease_expired' };
      }
      const tag = wrow.payload && typeof wrow.payload === 'object'
        ? wrow.payload.tag_id : null;
      const messageId = typeof tag === 'string' && tag
        ? await this.pendingOutbound(client, { conversationId: target, tagId: tag })
        : null;
      return { ok: true, messageId };
    }
    if (owner.kind === 'event_receipt') {
      const intents = this.receiptIntents ? await this.receiptIntents() : new Set();
      if (!intents.has(owner.id)) {
        return { ok: false, reason: 'no_journaled_receipt_intent' };
      }
      // Later-phase revocation: an event row that already closed cannot
      // collect another receipt (canonical digest, direct id or live alias).
      const ev = (await client.query(
        `SELECT e.state FROM bridge_event e
          LEFT JOIN bridge_event_alias a ON a.event_ref = e.id
         WHERE (e.source_digest = $1 OR e.id = $1 OR a.event_id = $1)
           AND e.platform = $2 AND e.account_key = $3`,
        [owner.id, this.platform, this.accountKey])).rows[0];
      if (ev && ev.state !== 'open') {
        return { ok: false, reason: 'receipt_event_terminal' };
      }
      return { ok: true, messageId: null };
    }
    if (owner.kind === 'otp_challenge') {
      if (!UUID_SHAPE.test(String(owner.id))) {
        return { ok: false, reason: 'malformed_otp_id' };
      }
      const ch = (await client.query(
        `SELECT platform FROM otp_challenge
          WHERE challenge_id = $1::uuid AND expires_at > now()`,
        [owner.id])).rows[0];
      if (!ch || ch.platform !== this.platform) {
        return { ok: false, reason: 'otp_unknown_or_expired' };
      }
      const messageId = await this.pendingOutbound(client, {
        conversationId: target, tagId: `otp-${owner.id}` });
      if (messageId == null) {
        return { ok: false, reason: 'otp_no_pending_outbound' };
      }
      return { ok: true, messageId };
    }
    if (owner.kind === 'live_invocation') {
      if (!/^\d+$/.test(String(owner.id))) {
        return { ok: false, reason: 'malformed_live_id' };
      }
      const messageId = await this.pendingOutbound(client, {
        conversationId: target, rowId: owner.id });
      if (messageId == null) {
        return { ok: false, reason: 'live_no_pending_outbound' };
      }
      return { ok: true, messageId };
    }
    return { ok: false, reason: 'unknown_owner_kind' };
  }

  /**
   * The committed pending outbound audit row an action confirms — by
   * `otp-{challenge}`/`tag_id` or by direct row id — scoped to this
   * platform and the action's target conversation. `messages.is_nora` and
   * `delivery_status='pending'` are the app's seam, the same predicates
   * `actions.py::_pending_outbound` uses.
   */
  async pendingOutbound(client, { conversationId, tagId = null, rowId = null }) {
    const params = [this.platform, conversationId];
    let sql = `SELECT id FROM messages
       WHERE is_nora = 1 AND delivery_status = 'pending'
         AND platform = $1 AND conversation_id = $2`;
    if (tagId != null) {
      params.push(tagId);
      sql += ` AND raw_json::jsonb ->> 'tag_id' = $${params.length}`;
    }
    if (rowId != null) {
      params.push(rowId);
      sql += ` AND id = $${params.length}`;
    }
    const row = (await client.query(sql, params)).rows[0];
    return row ? Number(row.id) : null;
  }

  /**
   * Terminal or 'started' state write (`action_state`). Scoped to this
   * bridge's platform/account and CAS'd on a live reservation — a late
   * finish cannot overwrite a row that already reached a verdict.
   */
  async finish(actionId, state, { platformRef = null, platformMs = null, error = null } = {}) {
    if (state === 'prepared' || !['started', ...TERMINAL].includes(state)) {
      throw new Error(`bad action state ${state}`);
    }
    const res = await this.pool.query(
      `UPDATE bridge_action
         SET state=$4, platform_ref=$5, platform_ms=$6, error=$7,
             started_at = COALESCE(started_at, now()),
             completed_at = CASE WHEN $4 <> 'started' THEN now() ELSE completed_at END
       WHERE action_id = $1 AND platform = $2 AND account_key = $3
             AND state IN ('prepared','started')`,
      [actionId, this.platform, this.accountKey, state, platformRef, platformMs,
        error ? JSON.stringify(error) : null],
    );
    return res.rowCount;
  }

  /** The recorded row for `GET /v1/actions/{id}` — this bridge's rows only. */
  async get(actionId) {
    const res = await this.pool.query(
      `SELECT * FROM bridge_action
       WHERE action_id = $1 AND platform = $2 AND account_key = $3`,
      [actionId, this.platform, this.accountKey]);
    return res.rows[0] || null;
  }
}
