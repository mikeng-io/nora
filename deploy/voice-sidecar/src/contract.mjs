// The envelope writer for the `voice` contract (design/service-api/
// conventions.md «The envelope», «Outcomes»; the kernel's
// `nora/domain/service_api.py`). Every answer on the voice routes is written here:
// six parts, status derived from the outcome. The answers without an envelope
// are 400, 401, 404, 408, 409, 413 and 422.

export const SOURCE = 'voice-controller';

/** The kernel's `_STATUS` table, verbatim. */
export const STATUS = Object.freeze({
  ok: 200, nothing_found: 200, unavailable: 503,
  complete: 200, pending: 202, denied: 403, rejected: 200,
  unsupported: 200, incomplete: 200, uncertain: 200,
});

/** The operation family's outcomes; `ok` and `nothing_found` are a read's. */
export const OPERATION_OUTCOMES = Object.freeze([
  'complete', 'pending', 'denied', 'rejected', 'unsupported', 'incomplete', 'uncertain', 'unavailable',
]);
export const ENVELOPE_PARTS = Object.freeze(['outcome', 'payload', 'when', 'from', 'scope', 'trace']);

const TRACEPARENT_RE = /^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/;

/** The trace id of the request's `traceparent`, or '' when it carries none. */
export function traceOf(req) {
  const m = TRACEPARENT_RE.exec(String(req?.headers?.traceparent ?? ''));
  if (!m || m[0].startsWith('ff') || /^0+$/.test(m[1]) || /^0+$/.test(m[2])) return '';
  return m[1];
}

const SCOPE = Object.freeze({ level: 'installation' });

/**
 * An operation envelope. `state` is the connection snapshot (null when no
 * connection resolved); `error` is `{kind, message}` on a refusal.
 */
export function operationEnvelope(trace, {
  outcome, state = null, error = null, target = '', startedAtMs = null, settledAtMs = null,
}) {
  return {
    outcome,
    payload: error ? { state, error } : { state },
    when: {
      state: outcome === 'pending' ? 'started' : 'settled',
      started_at_ms: startedAtMs,
      settled_at_ms: outcome === 'pending' ? null : settledAtMs,
    },
    from: { target, platform_ref: null },
    scope: { ...SCOPE },
    trace,
  };
}

/** A read envelope, stamped by this controller's own clock. */
export function readEnvelope(trace, { outcome, payload = null, observedAtMs, healthy = true }) {
  return {
    outcome,
    payload: outcome === 'ok' ? payload : null,
    when: { observed_at_ms: observedAtMs, known_by: 'controller_clock' },
    from: { source: SOURCE, healthy, failed_sources: [] },
    scope: { ...SCOPE },
    trace,
  };
}

export function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(JSON.stringify(body));
}

/** Write an envelope; the status is its outcome's. */
export function sendEnvelope(res, envelope) {
  return sendJson(res, STATUS[envelope.outcome], envelope);
}

/** One of the answers that carry no envelope. */
export function sendRefusal(res, status, error) {
  return sendJson(res, status, { error });
}
