import type { Plan } from "./api/people";
import { TIME_ZONE } from "./format";

// A plan's place in time, decided once for Today, the plans page and the
// command palette. A plan that started under three hours ago still counts
// as "now": dinner at seven is not over at five past.
const ONGOING_MS = 3 * 60 * 60 * 1000;

export const planTime = (p: Pick<Plan, "when">) => (p.when ? Date.parse(p.when) : null);

// A plan that is over or off is past whatever its date says.
const ENDED: ReadonlySet<Plan["status"]> = new Set(["happened", "cancelled", "lapsed", "evicted"]);

export function isUpcoming(p: Plan, now: number): boolean {
  if (ENDED.has(p.status)) return false;
  const at = planTime(p);
  return at === null ? true : at >= now - ONGOING_MS;
}

/** Dated plans soonest first, then the ones still waiting for a date. */
export function upcomingPlans(plans: Plan[], now: number): Plan[] {
  const up = plans.filter((p) => isUpcoming(p, now));
  return up.sort((a, b) => {
    const ta = planTime(a);
    const tb = planTime(b);
    if (ta === null && tb === null) return Date.parse(b.created_at) - Date.parse(a.created_at);
    if (ta === null) return 1;
    if (tb === null) return -1;
    return ta - tb;
  });
}

/** Past and cancelled plans, most recent first. */
export function pastPlans(plans: Plan[], now: number): Plan[] {
  return plans
    .filter((p) => !isUpcoming(p, now))
    .sort((a, b) => (planTime(b) ?? Date.parse(b.created_at)) - (planTime(a) ?? Date.parse(a.created_at)));
}

export function mapsUrl(p: Pick<Plan, "venue">): string | null {
  if (p.venue.maps_url) return p.venue.maps_url;
  const q = [p.venue.name, p.venue.address].filter(Boolean).join(", ") || p.venue.text;
  return q ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}` : null;
}

function icsEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/([,;])/g, "\\$1");
}

function icsStamp(ms: number): string {
  return new Date(ms).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

/** An iCalendar file for one dated plan, so it can go into anyone's own
 * calendar. Two hours long unless it says otherwise (it never does). */
export function planToIcs(p: Plan, now = Date.now()): string | null {
  const start = planTime(p);
  if (start === null) return null;
  const where = [p.venue.name, p.venue.address].filter(Boolean).join(", ") || p.venue.text || "";
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Nora//Portal//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${p.id}@nora`,
    `DTSTAMP:${icsStamp(now)}`,
    `DTSTART:${icsStamp(start)}`,
    `DTEND:${icsStamp(start + 2 * 60 * 60 * 1000)}`,
    `SUMMARY:${icsEscape(p.title)}`,
    where ? `LOCATION:${icsEscape(where)}` : null,
    mapsUrl(p) ? `URL:${mapsUrl(p)}` : null,
    p.status === "confirmed" || p.status === "happened" ? "STATUS:CONFIRMED" : "STATUS:TENTATIVE",
    "END:VEVENT",
    "END:VCALENDAR",
  ].filter(Boolean);
  return lines.join("\r\n") + "\r\n";
}

/** Month key in the display zone, for grouping ("2026-10"). */
export function monthKey(ms: number): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit" })
    .format(ms)
    .slice(0, 7);
}
