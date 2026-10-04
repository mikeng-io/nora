// The one layout both views of a Schedule are drawn from: the desktop grid places a block
// by `left`/`width` (percent of the axis), the phone list reads the same blocks in order.
// Pure, and defensive: the checker guarantees to > from, blocks inside the axis and no
// overlap inside a lane, but the renderer is the last line of defence and never throws or
// draws outside the axis whatever it is given.

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export type ScheduleInput = {
  start: string;
  end: string;
  lanes: Array<{
    name: string;
    blocks: Array<{ from: string; to: string; title: string; note?: string; tone?: Tone }>;
  }>;
};

export type LaidBlock = {
  title: string;
  note?: string;
  tone: Tone;
  /** "HH:MM", after clamping into the axis. */
  from: string;
  to: string;
  /** Minutes since 00:00 of the first day, after clamping. */
  fromMin: number;
  toMin: number;
  /** Percent of the axis, 0..100. */
  left: number;
  width: number;
  /** Which sub-row of the lane: 0 unless the lane (wrongly) overlaps itself. */
  row: number;
};

export type LaidLane = { name: string; blocks: LaidBlock[]; rows: number };
export type Tick = { min: number; left: number; hour: boolean; label: string | null };
export type Layout = { startMin: number; endMin: number; span: number; ticks: Tick[]; lanes: LaidLane[] };

/** Pixels per hour on a screen: 7 hours is 980px, wider than a phone, so it scrolls. */
export const PX_PER_HOUR = 140;
/** A block never draws narrower than this, so a 15-minute slot still shows a readable title. */
export const MIN_BLOCK_PX = 88;
const MIN_BLOCK_MIN = (MIN_BLOCK_PX / PX_PER_HOUR) * 60;
/** A label needs about 40px (22 minutes) after its tick; closer to the end it is left off. */
const LABEL_ROOM_MIN = 30;

export function parseClock(s: string): number | null {
  const m = /^([0-9]{1,2}):([0-9]{2})$/.exec(s);
  if (!m) return null;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  return h > 23 || mi > 59 ? null : h * 60 + mi;
}

export function formatClock(min: number): string {
  const m = Math.max(0, Math.round(min));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function layoutSchedule(input: ScheduleInput): Layout {
  const startMin = parseClock(input.start) ?? 0;
  const parsedEnd = parseClock(input.end);
  // A reversed or unreadable axis becomes one hour, so nothing divides by zero.
  const endMin = parsedEnd !== null && parsedEnd > startMin ? parsedEnd : startMin + 60;
  const span = endMin - startMin;
  const pct = (min: number) => ((min - startMin) / span) * 100;

  const ticks: Tick[] = [];
  for (let min = Math.ceil(startMin / 30) * 30; min <= endMin; min += 30) {
    const hour = min % 60 === 0;
    ticks.push({ min, left: pct(min), hour, label: hour && endMin - min >= LABEL_ROOM_MIN ? formatClock(min % 1440) : null });
  }

  const lanes = input.lanes.map((lane): LaidLane => {
    const blocks: LaidBlock[] = [];
    for (const b of lane.blocks) {
      const f = parseClock(b.from);
      const t = parseClock(b.to);
      if (f === null || t === null) continue;
      const fromMin = Math.max(f, startMin);
      const toMin = Math.min(t, endMin);
      if (toMin <= fromMin) continue;
      blocks.push({
        title: b.title,
        note: b.note,
        tone: b.tone ?? "neutral",
        from: formatClock(fromMin),
        to: formatClock(toMin),
        fromMin,
        toMin,
        left: pct(fromMin),
        width: (Math.min(Math.max(toMin - fromMin, MIN_BLOCK_MIN), endMin - fromMin) / span) * 100,
        row: 0,
      });
    }
    blocks.sort((a, c) => a.fromMin - c.fromMin || a.toMin - c.toMin);
    // Greedy sub-rows: a block goes in the first row that is free by its start.
    const rowEnds: number[] = [];
    for (const b of blocks) {
      let r = rowEnds.findIndex((end) => end <= b.fromMin);
      if (r === -1) r = rowEnds.length;
      // A short block is drawn wider than its time (MIN_BLOCK_PX), so the row is held that long.
      rowEnds[r] = b.fromMin + Math.max(b.toMin - b.fromMin, MIN_BLOCK_MIN);
      b.row = r;
    }
    return { name: lane.name, blocks, rows: Math.max(1, rowEnds.length) };
  });

  return { startMin, endMin, span, ticks, lanes };
}

/** The axis width in px on a screen. */
export const axisWidth = (span: number) => Math.round((span / 60) * PX_PER_HOUR);
