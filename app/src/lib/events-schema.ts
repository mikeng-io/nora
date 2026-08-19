import { z } from "zod";

// Wire-format schema for Nora's own `/app/v1/events(/{id})` pair
// (nora/web/events.py's `_event_dict`/`build_event_payload`) — this is
// local Postgres via EventService, not a remote REST API the way
// games-schema.ts's Pulse surface is, so there's no README/live-capture
// provenance comment to carry here: the shape below is read straight off
// `nora/domain/events.py`'s `Event`/`RSVP` dataclasses and the payload
// builder that serializes them.

const VenueSchema = z.object({
  text: z.string().nullable(),
  name: z.string().nullable(),
  address: z.string().nullable(),
  rating: z.number().nullable(),
  price_level: z.string(),
  maps_url: z.string().nullable(),
});
export type Venue = z.infer<typeof VenueSchema>;

const EventSchema = z.object({
  id: z.string(),
  title: z.string(),
  when: z.string().nullable(),
  venue: VenueSchema,
  status: z.enum(["proposed", "confirmed", "cancelled"]),
  created_by: z.string(),
  created_at: z.string(),
});
export type Event = z.infer<typeof EventSchema>;

const EventListSchema = z.array(EventSchema);

const RsvpSchema = z.object({
  person_id: z.string(),
  response: z.enum(["yes", "no", "maybe"]),
});
export type Rsvp = z.infer<typeof RsvpSchema>;

const EventDetailSchema = EventSchema.extend({
  rsvps: z.array(RsvpSchema),
});
export type EventDetail = z.infer<typeof EventDetailSchema>;

/** No conversation id — the list is participant-scoped, not
 * conversation-scoped: `personId` (resolved server-side by the proxy
 * route from the session, never client-supplied) is all `events_list`
 * needs, and it already only returns events this person created or
 * RSVP'd to. Throws on a non-ok response rather than papering over a 400
 * as "no events", the same discipline `fetchServers()` uses. */
export async function fetchEvents(): Promise<Event[]> {
  const res = await fetch("/api/events", { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`fetchEvents failed: ${res.status}`);
  }
  return EventListSchema.parse(await res.json());
}

export async function fetchEvent(id: string): Promise<EventDetail> {
  const res = await fetch(`/api/events/${encodeURIComponent(id)}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`fetchEvent failed: ${res.status}`);
  }
  return EventDetailSchema.parse(await res.json());
}
