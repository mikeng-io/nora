/**
 * Bounded OpenTelemetry trace surface for the durable capture path.
 *
 * THE CONTRACT (what makes a trace proof rather than decoration):
 *   - `bridge.capture` is the queue producer span: it is started before the
 *     journal fsync, its exported span context becomes `env.trace`, and it
 *     ends right after the append — the envelope's traceparent names a span
 *     that actually shipped, never a fabricated id. No span is held across
 *     the queue wait or a deployment gap.
 *   - Every later operation (custody, enqueue attempt + retries, receipt,
 *     action attempt) is a FRESH ROOT with explicit SpanLinks to the
 *     original capture span and to the previous attempt where one exists.
 *     Links, not parents: a drain minutes later must not reopen the
 *     capture span's tree as a child.
 *   - `nora.trace_kind` is a CREATION attribute, not a post-hoc label.
 *   - Errors are a bounded reason string, never the raw exception —
 *     `recordException` would leak SDK messages, stacks and any private
 *     material the error text carries.
 *   - `setAttribute` runs an allowlist, not arbitrary k/v.
 *
 * The exporter is the OTLP/HTTP span exporter under a BatchSpanProcessor:
 * finished spans leave on the processor's queue, so an exporter outage
 * drops spans, never events, and never blocks durable capture. Sampler and
 * collector routing are parent-owned — this side only emits to the
 * configured endpoint. When no endpoint is configured the whole surface is
 * a null object — the same callsites run against `?.` guards.
 *
 * Env: OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (preferred) or
 * OTEL_EXPORTER_OTLP_ENDPOINT (+ /v1/traces); OTEL_SERVICE_NAME overrides
 * the default `whatsapp-bridge` resource name.
 */
import { context, trace, SpanKind, SpanStatusCode } from '@opentelemetry/api';
import { NodeTracerProvider, BatchSpanProcessor } from '@opentelemetry/sdk-trace-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME } from '@opentelemetry/semantic-conventions';

// W3C traceparent: version (not ff), 32-hex trace id, 16-hex span id, flags.
// All-zero ids are spec-invalid — a zero context is not a link.
const TRACEPARENT = /^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;
const ZERO32 = '0'.repeat(32);
const ZERO16 = '0'.repeat(16);

const KINDS = {
  producer: SpanKind.PRODUCER, consumer: SpanKind.CONSUMER,
  client: SpanKind.CLIENT, internal: SpanKind.INTERNAL,
};

// The ONLY keys a span may carry. Ids, names, bodies, URLs and hostnames
// are deliberately absent — attributes are telemetry, not evidence bags.
const ATTR_ALLOWLIST = new Set([
  'nora.trace_kind',
  'messaging.system',
  'messaging.destination',
  'bridge.op',
  'bridge.owner_kind',
  'bridge.action.state',
  'bridge.custody.kind',
  'bridge.media.size',
]);
const MAX_ATTR_VALUE = 120;

// A span error is a category, not an exception: `timeout`, `enqueue_failed`,
// `{type:'redis', reason:'timeout'}` — bounded ascii labels only.
const SAFE_LABEL = /^[a-z][a-z0-9_.:-]{0,63}$/i;

export function parseTraceparent(tp) {
  const m = TRACEPARENT.exec(String(tp || ''));
  if (!m || m[1] === 'ff') return null;
  if (m[2] === ZERO32 || m[3] === ZERO16) return null;
  return { traceId: m[2], spanId: m[3], traceFlags: parseInt(m[4], 16), isRemote: true };
}

/** traceparent string -> OTel Link, or null when the context isn't valid. */
function linkOf(tp) {
  const ctx = parseTraceparent(tp);
  return ctx ? { context: ctx } : null;
}

function sanitizeAttributes(attrs) {
  const out = {};
  if (!attrs || typeof attrs !== 'object') return out;
  for (const [k, v] of Object.entries(attrs)) {
    if (!ATTR_ALLOWLIST.has(k)) continue;
    if (typeof v === 'string') out[k] = v.slice(0, MAX_ATTR_VALUE);
    else if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    else if (typeof v === 'boolean') out[k] = v;
    // every other type (objects, Errors, undefined) drops silently
  }
  return out;
}

function safeReason(err) {
  if (err == null) return null;
  if (typeof err === 'string' && SAFE_LABEL.test(err)) return err;
  if (typeof err === 'object' && !Array.isArray(err)) {
    const type = SAFE_LABEL.test(String(err.type || '')) ? String(err.type) : 'error';
    const reason = SAFE_LABEL.test(String(err.reason || '')) ? String(err.reason) : 'failed';
    return `${type}:${reason}`;
  }
  return 'failed';
}

function wrap(span) {
  return {
    span,
    setAttribute(k, v) {
      try {
        const clean = sanitizeAttributes({ [k]: v });
        if (k in clean) span.setAttribute(k, clean[k]);
      } catch { /* attr bugs must not break capture */ }
    },
    spanContext() { return span.spanContext(); },
    /** `err` is a safe reason label, NEVER the exception object. */
    end(err) {
      try {
        const reason = safeReason(err);
        if (reason) span.setStatus({ code: SpanStatusCode.ERROR, message: reason });
        span.end();
      } catch { /* tracing must never throw into the capture path */ }
    },
  };
}

export function otlpEndpoint(env = process.env) {
  if (env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT) return env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT;
  const base = env.OTEL_EXPORTER_OTLP_ENDPOINT;
  return base ? `${base.replace(/\/+$/, '')}/v1/traces` : '';
}

/**
 * `exporter` may be injected (tests pass InMemorySpanExporter); otherwise
 * `endpoint`/`env` drive the OTLP exporter. Returns null when disabled.
 */
export function initTracing({ serviceName, endpoint, exporter, env } = {}) {
  const url = endpoint ?? otlpEndpoint(env);
  if (!exporter && !url) return null;
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({
      [ATTR_SERVICE_NAME]: serviceName || env?.OTEL_SERVICE_NAME || 'whatsapp-bridge',
    }),
    spanProcessors: [new BatchSpanProcessor(exporter || new OTLPTraceExporter({ url }))],
  });
  const tracer = provider.getTracer('whatsapp-bridge');
  // Spans must be roots unless a parent is explicitly named — never inherit
  // whatever ambient context a caller happens to hold.
  const ROOT = trace.deleteSpan(context.active());

  return {
    provider,
    /**
     * Start a bounded span.
     *   parent     — traceparent string for an explicit parent (rare; links
     *                are the norm for this pipeline)
     *   links      — traceparent strings to attach as SpanLinks
     *   attributes — allowlisted creation-time attributes
     */
    startSpan(name, { parent = null, links = [], attributes = {}, kind = 'internal' } = {}) {
      const spanCtx = parseTraceparent(parent);
      const ctx = spanCtx ? trace.setSpanContext(ROOT, spanCtx) : ROOT;
      const otelLinks = (Array.isArray(links) ? links : []).map(linkOf).filter(Boolean);
      return wrap(tracer.startSpan(name, {
        kind: KINDS[kind] || SpanKind.INTERNAL,
        links: otelLinks,
        attributes: sanitizeAttributes(attributes),
      }, ctx));
    },
    /** Format a span context as a W3C traceparent — the envelope lineage. */
    traceparent(spanCtx) {
      if (!spanCtx || !spanCtx.traceId || !spanCtx.spanId) return null;
      if (spanCtx.traceId === ZERO32 || spanCtx.spanId === ZERO16) return null;
      return `00-${spanCtx.traceId}-${spanCtx.spanId}-${(spanCtx.traceFlags ?? 1).toString(16).padStart(2, '0')}`;
    },
    async flush() { try { await provider.forceFlush(); } catch { /* best-effort */ } },
    async shutdown() { try { await provider.shutdown(); } catch { /* best-effort */ } },
  };
}
