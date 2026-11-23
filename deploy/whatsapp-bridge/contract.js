/**
 * The envelope writer for the `platform` and `operations` contracts
 * (design/service-api/conventions.md «The envelope», «Outcomes»; the kernel's
 * `nora/domain/service_api.py`). Every answer on `/v1/platform/` and
 * `/v1/operations/` is written here: six parts, status derived from the
 * outcome. The only answers without an envelope are 400, 401, 404, 409, 422,
 * and the server's 413 for a JSON body over the cap.
 */
import { sendJson } from './http_util.js';
import { validateTraceparent } from './envelope.js';

export const SOURCE = 'whatsapp';

/** The kernel's `_STATUS` table, verbatim. */
export const STATUS = Object.freeze({
  ok: 200, nothing_found: 200, unavailable: 503,
  complete: 200, pending: 202, denied: 403, rejected: 200,
  unsupported: 200, incomplete: 200, uncertain: 200,
});

/** The trace id of the request's `traceparent`, or '' when it carries none. */
export function traceOf(req) {
  const tp = req?.headers?.traceparent;
  return validateTraceparent(tp) ? String(tp).split('-')[1] : '';
}

/** `room` when the request names one conversation, else `installation`. */
export function scopeOf(conversationId) {
  return typeof conversationId === 'string' && conversationId
    ? { level: 'room', id: conversationId } : { level: 'installation' };
}

function ms(value) {
  if (value == null) return null;
  const n = value instanceof Date ? value.getTime() : Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * The platform outcome of a stored `bridge_action` state («The wire» outcome
 * mapping). Storage keeps its own names; only this edge speaks outcomes.
 */
export function outcomeOf(state, error) {
  if (state === 'confirmed') return 'complete';
  if (state === 'uncertain') return 'uncertain';
  if (state === 'rejected') {
    if (error?.kind === 'unsupported') return 'unsupported';
    if (error?.kind === 'fenced') return 'denied';
    return 'rejected';
  }
  if (state === 'prepared' || state === 'started') return 'pending';
  return 'unavailable';
}

/** An operation envelope from an action result or a stored row. */
export function operationEnvelope(trace, action, target) {
  const outcome = outcomeOf(action.state, action.error);
  const refs = Array.isArray(action.platform_refs) ? action.platform_refs.map(String)
    : (action.platform_ref ? [String(action.platform_ref)] : []);
  return {
    outcome,
    payload: {
      platform_refs: refs,
      platform_ms: action.platform_ms ?? null,
      message_id: action.message_id == null ? null : Number(action.message_id),
      error: action.error ? { kind: String(action.error.kind),
        message: String(action.error.message ?? 'bridge operation failed') } : null,
    },
    when: {
      state: outcome === 'pending' ? action.state : 'settled',
      started_at_ms: ms(action.started_at),
      settled_at_ms: ms(action.completed_at),
    },
    from: { target: typeof target === 'string' ? target : '', platform_ref: action.platform_ref ?? null },
    scope: scopeOf(target),
    trace,
  };
}

/** Write an operation envelope; the status is the outcome's. */
export function sendOperation(res, envelope) {
  return sendJson(res, STATUS[envelope.outcome], envelope);
}

/** An envelope settled at once with no row behind it (blob custody, refusals). */
export function settledOperation(trace, { outcome, payload = null, target = '', scope = null }) {
  return {
    outcome, payload,
    when: { state: 'settled', started_at_ms: null, settled_at_ms: null },
    from: { target, platform_ref: null },
    scope: scope ?? scopeOf(null),
    trace,
  };
}

/**
 * A read envelope. `knownBy` names the clock `observedAtMs` came from:
 * 'bridge_clock' when the handler stamped it itself.
 */
export function readEnvelope(trace, { outcome, payload = null, healthy, scope = null,
  observedAtMs = Date.now(), knownBy = 'bridge_clock', failedSources = [] }) {
  return {
    outcome,
    payload: outcome === 'ok' ? payload : null,
    when: { observed_at_ms: observedAtMs, known_by: knownBy },
    from: { source: SOURCE, healthy: Boolean(healthy), failed_sources: failedSources },
    scope: scope ?? scopeOf(null),
    trace,
  };
}

export function sendRead(res, envelope) {
  return sendJson(res, STATUS[envelope.outcome], envelope);
}

/** One of the five answers that carry no envelope (400, 404, 409, 413, 422). */
export function sendRefusal(res, status, error) {
  return sendJson(res, status, { error });
}
