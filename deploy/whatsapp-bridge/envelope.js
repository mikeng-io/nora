/**
 * The v1 inbound envelope and canonical source identity — the JS mirror of
 * `nora/platform/bridge/envelope.py`, byte-for-byte.
 *
 * `source_key` is the canonical, un-hashed source tuple joined with U+001F —
 * private, never in telemetry labels or the API's public identifiers.
 * `event_id` is the opaque public identity, HMAC-derived under a versioned
 * correlation key: a key rotation changes aliases without changing the
 * canonical uniqueness the `bridge_event` table enforces.
 *
 * WhatsApp upstream identities (the `upstream` part, `normalize.js`):
 *   message  `m:{stanza_id}:{sender_id}`             stanza + resolved sender —
 *                                                   members may reuse an id
 *   edit     `e:{target_id}:{edit_id}`               each edit stanza is one
 *                                                   transition
 *   delete   `d:{target_id}:{revoke_id}`             each revoke stanza is one
 *                                                   transition
 *   reaction `r:{target_id}:{rid}:{sender}:{add|remove}`  the transition kind
 *                                                   rides in the identity: an
 *                                                   add→remove pair can reuse
 *                                                   the stanza id
 */
import { createHmac, createHash } from 'node:crypto';

export const ENVELOPE_VERSION = 1;
const SEP = '\x1f';
const KINDS = new Set(['message', 'edit', 'delete', 'reaction', 'summons']);

export function canonicalSourceKey({ platform, accountKey, conversationId, kind, upstream }) {
  if (!KINDS.has(kind)) {
    throw new Error(`unknown bridge kind ${JSON.stringify(kind)}`);
  }
  const parts = [platform, accountKey, conversationId, kind, upstream];
  for (const p of parts) {
    if (!p || typeof p !== 'string' || p.includes(SEP)) {
      throw new Error('source identity parts must be non-empty and separator-free');
    }
  }
  return parts.join(SEP);
}

/** The DB-level uniqueness digest — unkeyed on purpose. */
export function canonicalDigest(sourceKey) {
  return createHash('sha256').update(sourceKey, 'utf8').digest('hex');
}

/** `v{key_version}:{hmac}` — an opaque, unguessable alias of the source. */
export function eventId(sourceKey, keyBytes, keyVersion) {
  const digest = createHmac('sha256', keyBytes).update(sourceKey, 'utf8').digest('hex');
  return `v${keyVersion}:${digest}`;
}

/**
 * W3C `traceparent` — version, 32-hex trace id, 16-hex span id, flags.
 * All-zero ids are spec-invalid: a zero context links to nothing.
 */
export function validateTraceparent(value) {
  if (typeof value !== 'string') return false;
  const parts = value.split('-');
  if (parts.length !== 4) return false;
  if (parts[0] === 'ff') return false;
  if (parts[1].length !== 32 || parts[2].length !== 16) return false;
  if (parts[1] === '0'.repeat(32) || parts[2] === '0'.repeat(16)) return false;
  const hex = (s) => [...s].every((c) => '0123456789abcdef'.includes(c));
  return hex(parts[1]) && hex(parts[2]);
}

/** Build the complete stream field payload (design 02-wire §envelope). */
export function buildEnvelope({
  platform = 'whatsapp',
  accountKey,
  kind,
  upstream,
  conversationId,
  conversationType,
  senderId,
  senderName,
  sentMs,
  receivedMs,
  queuedMs,
  timeQuality,
  message,
  attachments = [],
  trace = {},
  receipt = null,
  admissionRev = null,
  correlationKey,
  keyVersion,
}) {
  const sourceKey = canonicalSourceKey({
    platform, accountKey, conversationId, kind, upstream,
  });
  const id = eventId(sourceKey, correlationKey, keyVersion);
  const cleanTrace = {};
  if (validateTraceparent(trace.traceparent)) cleanTrace.traceparent = trace.traceparent;
  if (typeof trace.tracestate === 'string' && trace.tracestate.length <= 512) {
    cleanTrace.tracestate = trace.tracestate;
  }
  return {
    v: ENVELOPE_VERSION,
    platform,
    account_key: accountKey,
    kind,
    source_key: sourceKey,
    event_id: id,
    conversation_id: conversationId,
    conversation_type: conversationType,
    sender_id: senderId,
    sender_name: senderName ?? null,
    sent_ms: sentMs ?? null,
    received_ms: receivedMs,
    queued_ms: queuedMs,
    time_quality: timeQuality === 'upstream' ? 'upstream' : 'bridge_local',
    message,
    attachments,
    trace: cleanTrace,
    receipt,
    admission_rev: admissionRev,
  };
}
