/**
 * WhatsApp JID normalization — the JS port of `nora/platform/whatsapp/jid.py`.
 * These forms are the contract: `conversation_id`, `sender_id`, mention values
 * and `dm_allowed_senders` are matched exact-string against identity/policy
 * data written in these shapes, so the two implementations must not drift.
 *
 * - phone form: `"+" + digits` (same shape Signal's E.164 values take)
 * - lid form:   bare digits (never a `+` — a LID is not a phone number)
 * - group:      the `@g.us` JID verbatim
 */
export const PHONE_SERVER = 's.whatsapp.net';
export const GROUP_SERVER = 'g.us';
export const LID_SERVER = 'lid';
export const NEWSLETTER_SERVER = 'newsletter';

/** `<user>[:<device>]@<server>` → {user, server, device|null}, or null. */
export function parseJid(jid) {
  if (typeof jid !== 'string') return null;
  const at = jid.lastIndexOf('@');
  if (at <= 0 || at === jid.length - 1) return null;
  const userPart = jid.slice(0, at);
  const colon = userPart.indexOf(':');
  const user = colon === -1 ? userPart : userPart.slice(0, colon);
  if (!user) return null;
  return { user, server: jid.slice(at + 1), device: colon === -1 ? null : userPart.slice(colon + 1) };
}

export const isGroupJid = (jid) => parseJid(jid)?.server === GROUP_SERVER;
export const isLidJid = (jid) => parseJid(jid)?.server === LID_SERVER;
export const isPhoneJid = (jid) => parseJid(jid)?.server === PHONE_SERVER;
export const isBroadcast = (jid) => parseJid(jid)?.server === 'broadcast';
export const isNewsletter = (jid) => parseJid(jid)?.server === NEWSLETTER_SERVER;

/** `"+85290000100"` — the one shape a phone number takes anywhere Nora matches. */
export function phoneForm(digits) {
  return `+${String(digits).trim().replace(/^\+/, '')}`;
}

/** Bare digits — the `lid` id-kind. Never a `+`. */
export function lidForm(digits) {
  return String(digits).trim().replace(/^\+/, '');
}

/**
 * What `policy.toml`/the projection matches on, exact-string: the group JID
 * verbatim, else `<phone>@s.whatsapp.net` when the chat's phone is known, else
 * the raw JID (e.g. an unresolved `@lid` chat).
 */
export function conversationIdFor(chatJid, resolvedPhone) {
  if (isGroupJid(chatJid)) return chatJid;
  if (resolvedPhone) return `${lidForm(resolvedPhone)}@${PHONE_SERVER}`;
  return chatJid;
}

/**
 * The one `sender_id` string identity data is matched against: phone form,
 * else lid form, else the raw JID (matches nothing — fails closed).
 */
export function senderForm(senderJid, senderPhone, senderLid) {
  if (senderPhone) return phoneForm(senderPhone);
  if (senderLid) return lidForm(senderLid);
  return senderJid;
}
