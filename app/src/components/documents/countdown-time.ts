// Time left to an event, in Hong Kong time (UTC+8, no daylight saving). The figure is
// computed from an injected `now` (epoch ms) so it is testable and the component can keep
// the clock to itself.

export type Remaining =
  | { kind: "days"; days: number }
  | { kind: "hours"; hours: number; minutes: number }
  | { kind: "today" }
  | { kind: "passed" };

const MINUTE = 60_000;
const DAY = 86_400_000;
const HK = 8 * 3_600_000;

/** The target instant, epoch ms; NaN when the date or time cannot be read. */
export function targetInstant(date: string, time?: string): number {
  return Date.parse(`${date}T${time ?? "00:00"}:00+08:00`);
}

/** The calendar date in Hong Kong at `now`, as YYYY-MM-DD. */
export function hkDate(now: number): string {
  return new Date(now + HK).toISOString().slice(0, 10);
}

/** Whole calendar days from one YYYY-MM-DD to a later one (both read as UTC dates, so no clock enters). */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY);
}

/**
 * Calendar days in Hong Kong when the event is on a later day (tomorrow is "1 day", whatever
 * the hour); hours and minutes later the same day; "today" on the event's own day once it has
 * started (a date with no time starts at 00:00, so it reads "today" all day); "passed" from
 * the next day. null when the target cannot be read.
 */
export function remaining(date: string, time: string | undefined, now: number): Remaining | null {
  const target = targetInstant(date, time);
  if (!Number.isFinite(target) || !Number.isFinite(now)) return null;
  const today = hkDate(now);
  if (date > today) {
    // A day with no time that is still ahead of today's date is a whole number of days away.
    return { kind: "days", days: daysBetween(today, date) };
  }
  const diff = target - now;
  if (diff > 0) {
    const total = Math.ceil(diff / MINUTE);
    return { kind: "hours", hours: Math.floor(total / 60), minutes: total % 60 };
  }
  return date === today ? { kind: "today" } : { kind: "passed" };
}
