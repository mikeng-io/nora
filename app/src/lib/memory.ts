import { dayIndex } from "./format";

// What Nora remembers arrives as plain sentences (Honcho's conclusions,
// nora/web/memory.py). Some carry a leading date; the portal pulls it out
// to lay the episodes along a line, and leaves any sentence without one
// exactly as written.
export interface Episode {
  date: string | null;
  text: string;
  raw: string;
}

const LEADING_DATE = /^\s*\[?(\d{4}-\d{2}-\d{2})\]?\s*(?:[—–:\-|·]\s*)?(.+)$/s;

export function parseEpisode(raw: string): Episode {
  const m = LEADING_DATE.exec(raw);
  if (m && !Number.isNaN(Date.parse(m[1]))) return { date: m[1], text: m[2].trim(), raw };
  return { date: null, text: raw.trim(), raw };
}

/** Dated episodes newest first; undated ones keep their given order after. */
export function orderEpisodes(raws: string[]): Episode[] {
  const parsed = raws.map(parseEpisode);
  const dated = parsed.filter((e) => e.date).sort((a, b) => (a.date! < b.date! ? 1 : -1));
  return [...dated, ...parsed.filter((e) => !e.date)];
}

/** One episode, the same one all day, a different one tomorrow. */
export function episodeOfTheDay(raws: string[], now: number): Episode | null {
  if (raws.length === 0) return null;
  const i = ((dayIndex(now) % raws.length) + raws.length) % raws.length;
  return parseEpisode(raws[i]);
}
