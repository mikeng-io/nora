import type { NamedRef, Server, Session } from "./api/games";
import { dayIndex } from "./format";

// Game-server facts decided once, for the list, a server's page and a
// player's page alike.

export type ServerState = "online" | "offline" | "restarting" | "local" | "unknown";

/** A container's Docker status says more than its online flag: "restarting"
 * is neither up nor down. A local-save game has no container at all. */
export function serverState(s: Pick<Server, "current">): ServerState {
  if (s.current === null) return "local";
  if (s.current.online) return "online";
  return /restart/i.test(s.current.status) ? "restarting" : "offline";
}

/** Uptime from the sample's own clock, so it never depends on the viewer's. */
export function uptimeMs(current: { timestamp: number; startedAt?: string }): number | null {
  if (!current.startedAt) return null;
  const started = Date.parse(current.startedAt);
  return Number.isNaN(started) ? null : Math.max(0, current.timestamp - started);
}

/** A game's own name for something, in the reader's language. An entity
 * Pulse could not resolve (`known: false`) keeps its raw id; the caller
 * shows it dimmed and monospaced rather than trusting it as a name. */
export function refName(ref: Pick<NamedRef, "en" | "zhHant">, locale: string): string {
  return locale.startsWith("zh") ? ref.zhHant || ref.en : ref.en || ref.zhHant;
}

export const localized = (en: string | undefined, zh: string | undefined, locale: string) =>
  (locale.startsWith("zh") ? zh || en : en || zh) ?? "";

/** Pulse's heatmap counts weekdays from Sunday (0); the portal's week starts
 * on Monday, like the rhythm bins. */
export const sundayFirstToMondayFirst = (weekday: number) => (weekday + 6) % 7;

/** Playtime per calendar day (display zone) over the last `days`, from a
 * player's sessions, splitting a session that crosses midnight. An open
 * session counts up to `now`. */
export function playtimeByDay(sessions: Session[], days: number, now: number): Array<{ day: number; ms: number }> {
  const today = dayIndex(now);
  const out = new Map<number, number>();
  for (let d = today - days + 1; d <= today; d++) out.set(d, 0);
  for (const s of sessions) {
    const end = s.leftAt ?? now;
    let t = s.joinedAt;
    while (t < end) {
      const d = dayIndex(t);
      // The next display-zone midnight after t.
      let next = t + 60_000;
      while (next < end && dayIndex(next) === d) next = Math.min(end, next + 3_600_000);
      if (dayIndex(next) !== d) {
        // Walk back to the exact minute the day changed.
        let lo = next - 3_600_000;
        while (dayIndex(lo + 60_000) === d) lo += 60_000;
        next = lo + 60_000;
      }
      const slice = Math.min(end, next) - t;
      if (out.has(d)) out.set(d, out.get(d)! + slice);
      t = Math.min(end, next);
    }
  }
  return [...out].map(([day, ms]) => ({ day, ms }));
}
