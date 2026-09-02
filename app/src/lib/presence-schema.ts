import { z } from "zod";

// Wire-format schemas for nora/web/presence.py's GET /app/v1/presence and
// GET /app/v1/presence/{id}/history — mirrors that module's real payload
// shape exactly (camelCase keys, optional fields omitted rather than null,
// matching PresenceFix's own optionality).

// The collector polls every 5s while `is_driving` (config.toml's
// cadence_moving_seconds) — a fix older than this many seconds despite the
// flag still being true means Life360 itself hasn't reported a new
// position, not that our polling is too slow. Shared here (not in
// presence-map.tsx) because that file is Leaflet-dependent and only ever
// imported via next/dynamic(ssr:false) — a plain page-level import of a
// constant from it would pull Leaflet into that page's normal bundle.
export const STALE_DRIVING_SECONDS = 60;

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

export const PresenceAnalyticsPlaceSchema = z.object({
  kind: z.string(),
  label: z.string().nullable(),
  lat: z.number(),
  lng: z.number(),
  radiusM: z.number(),
  confidence: z.number(),
  firstSeenTs: z.number(),
  lastSeenTs: z.number(),
  stayCount: z.number(),
  totalDwellMs: z.number(),
});

export const PresenceAnalyticsPlacesSubjectSchema = z.object({
  subjectId: z.string(),
  places: z.array(PresenceAnalyticsPlaceSchema),
});

export const PresenceAnalyticsPlacesSchema = z.object({
  subjects: z.array(PresenceAnalyticsPlacesSubjectSchema),
});

export const PresenceRhythmBinSchema = z.object({
  hourOfWeek: z.number(),
  stayCount: z.number(),
  totalDwellMs: z.number(),
});

export const PresenceRhythmSchema = z.object({
  subjectId: z.string(),
  bins: z.array(PresenceRhythmBinSchema),
  windowDays: z.number(),
});

export const PresenceRouteTripSchema = z.object({
  id: z.number(),
  startedTs: z.number(),
  endedTs: z.number(),
  distanceM: z.number(),
  avgSpeedKmh: z.number(),
  fixCount: z.number(),
  corridorId: z.number().nullable(),
  corridorSampleCount: z.number().nullable(),
});

export const PresenceRouteCorridorSchema = z.object({
  id: z.number(),
  sampleCount: z.number(),
  firstSeenTs: z.number(),
  lastSeenTs: z.number(),
  sequence: z.array(z.string()),
});

export const PresenceRoutesSchema = z.object({
  subjectId: z.string(),
  trips: z.array(PresenceRouteTripSchema),
  corridors: z.array(PresenceRouteCorridorSchema),
});

export type PresenceFix = z.infer<typeof PresenceFixSchema>;
export type PresenceEntry = z.infer<typeof PresenceEntrySchema>;
export type PresencePlace = z.infer<typeof PresencePlaceSchema>;
export type PresenceHistoryPoint = z.infer<typeof PresenceHistoryPointSchema>;
export type PresenceHistory = z.infer<typeof PresenceHistorySchema>;
export type PresenceAnalyticsPlace = z.infer<typeof PresenceAnalyticsPlaceSchema>;
export type PresenceAnalyticsPlacesSubject = z.infer<typeof PresenceAnalyticsPlacesSubjectSchema>;
export type PresenceAnalyticsPlaces = z.infer<typeof PresenceAnalyticsPlacesSchema>;
export type PresenceRhythmBin = z.infer<typeof PresenceRhythmBinSchema>;
export type PresenceRhythm = z.infer<typeof PresenceRhythmSchema>;
export type PresenceRouteTrip = z.infer<typeof PresenceRouteTripSchema>;
export type PresenceRouteCorridor = z.infer<typeof PresenceRouteCorridorSchema>;
export type PresenceRoutes = z.infer<typeof PresenceRoutesSchema>;

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

export async function fetchPresenceAnalyticsPlaces(): Promise<PresenceAnalyticsPlaces> {
  const res = await fetch("/api/presence/analytics/places", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`presence analytics places fetch failed: ${res.status}`);
  }
  return PresenceAnalyticsPlacesSchema.parse(await res.json());
}

async function fetchPresenceAnalytics<T>(
  path: string,
  schema: z.ZodType<T>,
  errorName: string,
): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${errorName} fetch failed: ${res.status}`);
  }
  return schema.parse(await res.json());
}

export function fetchPresenceRhythm(subjectId: string, days = 30): Promise<PresenceRhythm> {
  const params = new URLSearchParams({ subjectId, days: String(days) });
  return fetchPresenceAnalytics(
    `/api/presence/analytics/rhythm?${params.toString()}`,
    PresenceRhythmSchema,
    "presence rhythm",
  );
}

export function fetchPresenceRoutes(subjectId: string, days = 30): Promise<PresenceRoutes> {
  const params = new URLSearchParams({ subjectId, days: String(days) });
  return fetchPresenceAnalytics(
    `/api/presence/analytics/routes?${params.toString()}`,
    PresenceRoutesSchema,
    "presence routes",
  );
}
