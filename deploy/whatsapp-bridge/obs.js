/**
 * Bridge observability: low-cardinality Prometheus metrics + a sanitized
 * JSON lifecycle line for the durable path.
 *
 * Hard rules (design 04 §observability): labels are closed enums only —
 * never an event id, conversation id, JID, phone, action id, or any
 * sender-supplied content. The lifecycle log may carry opaque correlation
 * ids (`event_id`, `action_id`, stream ids — HMAC aliases, not platform
 * identity) and outcome kinds; never bodies, JIDs, numbers, or credentials.
 * Inspection failures are unavailable values, never a phantom zero: a
 * gauge whose source broke stays unset rather than reporting 0.
 */
export class Metrics {
  constructor() {
    this.counters = new Map();
    this.gauges = new Map();
  }

  static _key(name, labels) {
    const parts = Object.keys(labels || {}).sort()
      .map((k) => `${k}="${String(labels[k]).replace(/["\\]/g, '_')}"`).join(',');
    return `${name}{${parts}}`;
  }

  inc(name, labels = {}, n = 1) {
    const key = Metrics._key(name, labels);
    this.counters.set(key, (this.counters.get(key) || 0) + n);
  }

  set(name, labels = {}, value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return;
    this.gauges.set(Metrics._key(name, labels), value);
  }

  /** `null` removes the series — an inspection that failed is absent, not 0. */
  unset(name, labels = {}) {
    this.gauges.delete(Metrics._key(name, labels));
  }

  text() {
    const out = [];
    for (const [key, v] of [...this.counters.entries()].sort()) out.push(`${key} ${v}`);
    for (const [key, v] of [...this.gauges.entries()].sort()) out.push(`${key} ${v}`);
    return `${out.join('\n')}\n`;
  }
}

/**
 * Structured lifecycle line for the durable capture/action path. `fields`
 * must only contain the values the module docstring allows.
 */
export function jlog(event, fields = {}) {
  console.log(JSON.stringify({ ts: Date.now(), event, ...fields }));
}
