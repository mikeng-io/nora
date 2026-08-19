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

/** `conversation_id` is required by nora/web/api.py's `events_list` (400 if
 * missing) — the proxy route forwards it through untouched, so this throws
 * the same way `fetchServers()` does on a non-ok response rather than
 * papering over a 400 as "no events". */
export async function fetchEvents(conversationId: string): Promise<Event[]> {
  const res = await fetch(
    `/api/events?conversation_id=${encodeURIComponent(conversationId)}`,
    { cache: "no-store" },
  );
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
