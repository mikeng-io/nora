import type { DerivedPlace, Fix, PresenceEntry, SavedPlace } from "./api/presence";
import { STALE_DRIVING_SECONDS } from "./api/presence";

// What one person's latest fix means, decided once here so the map, the
// people list, a profile and Today all say the same thing about them.

export type PresenceKind =
  | "driving" // live, moving
  | "driving-stale" // flagged driving, but no new position for a while
  | "live" // live, still
  | "stale" // last seen a while ago
  | "sharing_off"
  | "disconnected"
  | "no_fix"
  | "unavailable" // the collector couldn't check: never shown as "nowhere"
  | "none"; // no fix recorded at all

export const LOW_BATTERY = 20;

export function presenceKind(entry: Pick<PresenceEntry, "current">): PresenceKind {
  const fix = entry.current;
  if (!fix) return "none";
  switch (fix.wireState) {
    case "live":
      if (fix.isDriving) return fix.ageSeconds > STALE_DRIVING_SECONDS ? "driving-stale" : "driving";
      return "live";
    case "stale":
      return "stale";
    default:
      return fix.wireState;
  }
}

/** Current enough to put on the map as "here now" and to pulse. */
export const isLive = (kind: PresenceKind) => kind === "live" || kind === "driving";

/** Has a position worth drawing at all (even an old one, drawn as old). */
export const hasPosition = (entry: Pick<PresenceEntry, "current">) =>
  entry.current !== null && entry.current.wireState !== "unavailable";

/** Sort order for a list of people: moving, here, then fading out. */
const ORDER: PresenceKind[] = [
  "driving",
  "live",
  "driving-stale",
  "stale",
  "disconnected",
  "sharing_off",
  "no_fix",
  "unavailable",
  "none",
];
export const kindRank = (kind: PresenceKind) => ORDER.indexOf(kind);

export function sortPresence<T extends PresenceEntry>(entries: T[]): T[] {
  return [...entries].sort(
    (a, b) =>
      kindRank(presenceKind(a)) - kindRank(presenceKind(b)) ||
      (a.current?.ageSeconds ?? Infinity) - (b.current?.ageSeconds ?? Infinity) ||
      a.displayName.localeCompare(b.displayName),
  );
}

export function haversineM(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const s =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** Saved places match within this radius (Life360's own place radius is of
 * this order; a derived place brings its own radius). */
export const SAVED_PLACE_RADIUS_M = 150;

export type NamedPlace =
  | { source: "saved"; name: string }
  | { source: "derived"; kind: string; label: string | null };

/** Where a fix is, in words someone would use: a saved place's name, or one
 * of their own derived places (home, work), or nothing, and the caller
 * falls back to the street address. Only for a still fix: someone driving
 * past home is not "at home". */
export function namePlace(
  fix: Pick<Fix, "lat" | "lng" | "isDriving">,
  saved: SavedPlace[] | undefined,
  derived: DerivedPlace[] | undefined,
): NamedPlace | null {
  if (fix.isDriving) return null;
  let best: { d: number; place: NamedPlace } | null = null;
  for (const p of saved ?? []) {
    const d = haversineM(fix, p);
    if (d <= SAVED_PLACE_RADIUS_M && (!best || d < best.d)) best = { d, place: { source: "saved", name: p.name } };
  }
  if (best) return best.place;
  for (const p of derived ?? []) {
    if (p.confidence < 0.5 || (p.kind !== "home" && p.kind !== "work" && !p.label)) continue;
    const d = haversineM(fix, p);
    if (d <= Math.max(p.radiusM, 60) && (!best || d < best.d)) {
      best = { d, place: { source: "derived", kind: p.kind, label: p.label } };
    }
  }
  return best?.place ?? null;
}
