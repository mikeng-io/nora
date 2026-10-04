// The arithmetic of a donut, apart from the drawing so it can be tested. Positive values
// only (a share of a whole has no negative part); a total of nothing draws nothing.

/** Chart palette, in a fixed order: a slice keeps its colour by its place in the data. The
 * legend carries every number, so identity never rests on hue alone. */
export const DONUT_COLORS = ["denim", "blush", "bun", "success", "caution", "denim-dim", "ink-soft", "seam"] as const;

export interface DonutSlice {
  label: string;
  value: number;
  /** Share of the total, 0 to 100. */
  pct: number;
  /** Where the arc starts around the ring, in percent of the circumference. */
  start: number;
  color: (typeof DONUT_COLORS)[number];
}

export function donutSlices(data: Array<{ label: string; value: number }>): { total: number; slices: DonutSlice[] } {
  const kept = data
    .map((d, i) => ({ ...d, i }))
    .filter((d) => Number.isFinite(d.value) && d.value > 0);
  const total = kept.reduce((n, d) => n + d.value, 0);
  if (total <= 0) return { total: 0, slices: [] };
  let start = 0;
  const slices = kept.map((d) => {
    const pct = (d.value / total) * 100;
    const s: DonutSlice = { label: d.label, value: d.value, pct, start, color: DONUT_COLORS[d.i % DONUT_COLORS.length] };
    start += pct;
    return s;
  });
  return { total, slices };
}

/** "34%", or "<1%" for a sliver that would round to nothing. */
export function formatPct(pct: number): string {
  return pct > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
}
