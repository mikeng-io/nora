import { z } from "zod";

// Wire-format schemas for nora/web/presence.py's GET /app/v1/presence and
// GET /app/v1/presence/{id}/history — mirrors that module's real payload
// shape exactly (camelCase keys, optional fields omitted rather than null,
// matching PresenceFix's own optionality).

export const PresenceFixSchema = z.object({
  lat: z.number(),
  lng: z.number(),
  ts: z.number(),
  ageSeconds: z.number(),
  wireState: z.enum(["live", "stale", "sharing_off", "disconnected", "no_fix", "unavailable"]),
  batteryPercent: z.number().optional(),
  isDriving: z.boolean().optional(),
  speedKmh: z.number().optional(),
  address: z.string().optional(),
});

export const PresenceEntrySchema = z.object({
  personId: z.string(),
  displayName: z.string(),
  current: PresenceFixSchema.nullable(),
});

export const PresencePlaceSchema = z.object({
  name: z.string(),
  lat: z.number(),
  lng: z.number(),
});

export const PresenceHistoryPointSchema = z.object({
  lat: z.number(),
  lng: z.number(),
  ts: z.number(),
});

export const PresenceHistorySchema = z.object({
  hours: z.number(),
  from: z.number(),
  to: z.number(),
  points: z.array(PresenceHistoryPointSchema),
});

export type PresenceFix = z.infer<typeof PresenceFixSchema>;
export type PresenceEntry = z.infer<typeof PresenceEntrySchema>;
export type PresencePlace = z.infer<typeof PresencePlaceSchema>;
export type PresenceHistoryPoint = z.infer<typeof PresenceHistoryPointSchema>;
export type PresenceHistory = z.infer<typeof PresenceHistorySchema>;

const PresenceListSchema = z.array(PresenceEntrySchema);
const PresencePlacesSchema = z.array(PresencePlaceSchema);

export async function fetchPresence(): Promise<PresenceEntry[]> {
  const res = await fetch("/api/presence", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`presence fetch failed: ${res.status}`);
  }
  return PresenceListSchema.parse(await res.json());
}

export async function fetchPresencePlaces(): Promise<PresencePlace[]> {
  const res = await fetch("/api/presence/places", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`presence places fetch failed: ${res.status}`);
  }
  return PresencePlacesSchema.parse(await res.json());
}

export async function fetchPresenceHistory(personId: string, hours = 24): Promise<PresenceHistory> {
  const res = await fetch(
    `/api/presence/${encodeURIComponent(personId)}/history?hours=${hours}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw new Error(`presence history fetch failed: ${res.status}`);
  }
  return PresenceHistorySchema.parse(await res.json());
}
