// The shared service bearer (NORA_BRIDGE_TOKEN), checked the way the bridges
// check it: `bridge_helpers.js` `authorizedBridgeRequest`, copied rather than
// imported because this image builds from deploy/voice-sidecar alone.
//
// Byte lengths are compared before `timingSafeEqual`, which throws on a length
// mismatch; a non-ASCII header can have the expected string length and a
// different byte length.
import { timingSafeEqual } from 'node:crypto';

export function bearerMatches(authorization, token) {
  if (!token || typeof authorization !== 'string') return false;
  const got = Buffer.from(authorization, 'utf8');
  const expected = Buffer.from(`Bearer ${token}`, 'utf8');
  if (got.length !== expected.length) return false;
  return timingSafeEqual(got, expected);
}
