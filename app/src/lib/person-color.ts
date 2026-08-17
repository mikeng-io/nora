// Stable per-person accent so the same person reads as the same color
// everywhere in the portal (memory card, map pins/route). Pure display
// hash — not an identifier, never persisted or sent anywhere.
export function hueOf(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
}
