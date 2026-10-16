import { z } from "zod";
import { apiGet } from "./client";

// nora/web/persons.py: identity.toml's roster, and one person's profile
// (their cross-platform identity plus the game servers that know them).
export const PersonSummarySchema = z.object({
  personId: z.string(),
  displayName: z.string(),
});
export type PersonSummary = z.infer<typeof PersonSummarySchema>;
export const RosterSchema = z.array(PersonSummarySchema);

export const GameProfileSchema = z.object({
  serverId: z.string(),
  displayName: z.string(),
  game: z.string().nullable(),
});
export type GameProfile = z.infer<typeof GameProfileSchema>;

export const PersonProfileSchema = z.object({
  personId: z.string(),
  displayName: z.string(),
  gameProfiles: z.array(GameProfileSchema),
});
export type PersonProfile = z.infer<typeof PersonProfileSchema>;

export const fetchRoster = (signal?: AbortSignal) => apiGet("/persons", RosterSchema, { signal });
export const fetchPerson = (personId: string, signal?: AbortSignal) =>
  apiGet(`/persons/${encodeURIComponent(personId)}`, PersonProfileSchema, { signal });

// nora/web/gatherings.py (served as /app/v1/events): viewer-scoped.
// The proxy forces `personId` from the session, so the list is every event
// the viewer created or answered plus every event in a room they are in, and
// a detail read of any other event is 404.
const VenueSchema = z.object({
  text: z.string().nullable(),
  name: z.string().nullable(),
  address: z.string().nullable(),
  rating: z.number().nullable(),
  price_level: z.string(),
  maps_url: z.string().nullable(),
});
export type Venue = z.infer<typeof VenueSchema>;

export const PlanSchema = z.object({
  id: z.string(),
  title: z.string(),
  when: z.string().nullable(),
  venue: VenueSchema,
  // Every `GatheringStatus` the backend can send (nora/domain/gatherings.py).
  // A status missing here fails the whole list's parse and blanks the page.
  status: z.enum(["staged", "proposed", "confirmed", "happened", "cancelled", "lapsed", "evicted"]),
  created_by: z.string(),
  created_at: z.string(),
});
export type Plan = z.infer<typeof PlanSchema>;
export const PlansSchema = z.array(PlanSchema);

export const RsvpSchema = z.object({
  person_id: z.string(),
  response: z.enum(["yes", "no", "maybe"]),
});
export type Rsvp = z.infer<typeof RsvpSchema>;

export const PlanDetailSchema = PlanSchema.extend({ rsvps: z.array(RsvpSchema) });
export type PlanDetail = z.infer<typeof PlanDetailSchema>;

export const fetchPlans = (signal?: AbortSignal) => apiGet("/events", PlansSchema, { signal });
export const fetchPlan = (id: string, signal?: AbortSignal) =>
  apiGet(`/events/${encodeURIComponent(id)}`, PlanDetailSchema, { signal });
