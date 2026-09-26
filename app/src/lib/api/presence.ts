import { z } from "zod";
import { apiGet } from "./client";

// nora/web/presence.py and the analytics routes in nora/web/api.py:
// camelCase keys, optional fields omitted rather than null.

/** A fix older than this while `isDriving` is still true means Life360 has
 * not reported a new position, not that the collector is slow (it polls
 * every 5s while moving: config.toml's cadence_moving_seconds). */
export const STALE_DRIVING_SECONDS = 60;

export const WIRE_STATES = [
  "live",
  "stale",
  "sharing_off",
  "disconnected",
  "no_fix",
  "unavailable",
] as const;
export type WireState = (typeof WIRE_STATES)[number];

export const FixSchema = z.object({
  lat: z.number(),
  lng: z.number(),
  ts: z.number(),
  ageSeconds: z.number(),
  wireState: z.enum(WIRE_STATES),
  batteryPercent: z.number().optional(),
  isDriving: z.boolean().optional(),
  speedKmh: z.number().optional(),
  address: z.string().optional(),
});
export type Fix = z.infer<typeof FixSchema>;

export const PresenceEntrySchema = z.object({
  personId: z.string(),
  displayName: z.string(),
  current: FixSchema.nullable(),
});
export type PresenceEntry = z.infer<typeof PresenceEntrySchema>;

export const SavedPlaceSchema = z.object({ name: z.string(), lat: z.number(), lng: z.number() });
export type SavedPlace = z.infer<typeof SavedPlaceSchema>;

export const HistorySchema = z.object({
  hours: z.number(),
  from: z.number(),
  to: z.number(),
  points: z.array(z.object({ lat: z.number(), lng: z.number(), ts: z.number() })),
});
export type History = z.infer<typeof HistorySchema>;
export type HistoryPoint = History["points"][number];

export const DerivedPlaceSchema = z.object({
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
export type DerivedPlace = z.infer<typeof DerivedPlaceSchema>;

export const DerivedPlacesSchema = z.object({
  subjects: z.array(z.object({ subjectId: z.string(), places: z.array(DerivedPlaceSchema) })),
});
export type DerivedPlaces = z.infer<typeof DerivedPlacesSchema>;

/** hourOfWeek 0..167, Monday 00:00 first, bucketed server-side in HK time. */
export const RhythmSchema = z.object({
  subjectId: z.string(),
  bins: z.array(
    z.object({ hourOfWeek: z.number(), stayCount: z.number(), totalDwellMs: z.number() }),
  ),
  windowDays: z.number(),
});
export type Rhythm = z.infer<typeof RhythmSchema>;

export const TripSchema = z.object({
  id: z.number(),
  startedTs: z.number(),
  endedTs: z.number(),
  distanceM: z.number(),
  avgSpeedKmh: z.number(),
  fixCount: z.number(),
  corridorId: z.number().nullable(),
  corridorSampleCount: z.number().nullable(),
});
export type Trip = z.infer<typeof TripSchema>;

export const CorridorSchema = z.object({
  id: z.number(),
  sampleCount: z.number(),
  firstSeenTs: z.number(),
  lastSeenTs: z.number(),
  sequence: z.array(z.string()),
});
export type Corridor = z.infer<typeof CorridorSchema>;

export const RoutesSchema = z.object({
  subjectId: z.string(),
  trips: z.array(TripSchema),
  corridors: z.array(CorridorSchema),
});
export type Routes = z.infer<typeof RoutesSchema>;

export const fetchPresence = (signal?: AbortSignal) =>
  apiGet("/presence", z.array(PresenceEntrySchema), { signal });
export const fetchSavedPlaces = (signal?: AbortSignal) =>
  apiGet("/presence/places", z.array(SavedPlaceSchema), { signal });
export const fetchHistory = (personId: string, hours: number, signal?: AbortSignal) =>
  apiGet(`/presence/${encodeURIComponent(personId)}/history`, HistorySchema, {
    query: { hours },
    signal,
  });
export const fetchDerivedPlaces = (signal?: AbortSignal) =>
  apiGet("/presence/analytics/places", DerivedPlacesSchema, { signal });
export const fetchRhythm = (subjectId: string, days: number, signal?: AbortSignal) =>
  apiGet("/presence/analytics/rhythm", RhythmSchema, { query: { subjectId, days }, signal });
export const fetchRoutes = (subjectId: string, days: number, signal?: AbortSignal) =>
  apiGet("/presence/analytics/routes", RoutesSchema, { query: { subjectId, days }, signal });
