import type { Event } from "@/lib/events-schema";

// Shared between the list and detail pages so the status→badge mapping and
// date formatting can't drift between the two views of the same field.

export const STATUS_BADGE_VARIANT: Record<Event["status"], "live" | "designed" | "danger"> = {
  proposed: "designed",
  confirmed: "live",
  cancelled: "danger",
};

// Same HK-timezone, short-date convention as games-format.ts's
// `formatDateTime` — not imported from there directly since that module's
// formatters take a millisecond timestamp (Pulse's wire format) while an
// event's `when` is an ISO datetime string (Nora's own wire format).
const DISPLAY_TIME_ZONE = "Asia/Hong_Kong";

export function formatWhen(when: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: DISPLAY_TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(when));
}
