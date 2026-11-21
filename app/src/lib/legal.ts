// Constants the public privacy and terms pages share. The words live in
// messages/*.json (`legal`); the values that must be identical in both
// languages, and are not words, live here.
/** The instance operator's contact address, from LEGAL_CONTACT. Read when the
 * page is served, so each self-hosted Nora names its own operator. */
export function legalContact(): string {
  const contact = process.env.LEGAL_CONTACT?.trim();
  if (!contact) throw new Error("LEGAL_CONTACT is not set: the legal pages need the operator's contact address");
  return contact;
}
export const LEGAL_EFFECTIVE = "2026-10-11";
export const GOOGLE_USER_DATA_POLICY =
  "https://developers.google.com/terms/api-services-user-data-policy";
export const GOOGLE_PERMISSIONS = "https://myaccount.google.com/permissions";
