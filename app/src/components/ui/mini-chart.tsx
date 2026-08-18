"use client";

import { Fragment, useRef, useState } from "react";

// A dependency-free chart primitive — hand-rolled SVG, same approach the
// original games/[id]/page.tsx sparkline used, generalized so the player
// profile's playtime/session charts don't need to pull in a charting
// library just to draw a handful of bars/lines.

export interface ChartPoint {
  timestamp: number;
  value: number;
}

export interface ChartLine {
  label: string;
  color: string;
  points: ChartPoint[];
}

/** An interactive time-series line chart: Y-axis min/max, X-axis start/end
 * time, and a hover crosshair + tooltip reading the exact value under the
 * pointer — the plain `<path>` sparkline this replaced had none of that,
 * which is unreadable for anything but "line goes up/down". */
export function LineChart({
  series,
  height = 90,
  formatValue,
  formatTime,
  yMin,
  yMax,
}: {
  series: ChartLine[];
  height?: number;
  formatValue: (v: number) => string;
  formatTime: (t: number) => string;
  /** Fixed Y domain (e.g. 0–100 for a percent) — omit to fit the data. */
  yMin?: number;
  yMax?: number;
}) {
  const width = 600;
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const pointCount = series[0]?.points.length ?? 0;
  const allValues = series.flatMap((s) => s.points.map((p) => p.value));
  const max = yMax ?? Math.max(1, ...allValues);
  const min = yMin ?? Math.min(0, ...allValues);
  const range = Math.max(1e-6, max - min);
  const stepX = pointCount > 1 ? width / (pointCount - 1) : 0;
  const yFor = (v: number) => height - ((v - min) / range) * height;

  if (pointCount === 0) return null;

  const handleMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!svgRef.current || pointCount <= 1) return;
    const rect = svgRef.current.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * width;
    const idx = Math.round(relX / stepX);
    setHoverIndex(Math.max(0, Math.min(pointCount - 1, idx)));
  };

  const hoverPct = hoverIndex !== null ? (hoverIndex / (pointCount - 1 || 1)) * 100 : null;

  return (
    <div>
      <div className="flex">
        <div
          className="flex w-12 shrink-0 flex-col justify-between pr-2 text-right font-mono text-[9px] text-text-muted"
          style={{ height }}
        >
          <span>{formatValue(max)}</span>
          <span>{formatValue(min)}</span>
        </div>
        <div className="relative min-w-0 flex-1">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            className="block w-full cursor-crosshair"
            style={{ height }}
            onPointerMove={handleMove}
            onPointerLeave={() => setHoverIndex(null)}
            role="img"
            aria-hidden
          >
            <line
              x1={0} y1={height / 2} x2={width} y2={height / 2}
              stroke="var(--border)" strokeWidth={1} strokeDasharray="2,3"
            />
            {series.map((s) => (
              <path
                key={s.label}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                d={s.points
                  .map((p, i) => `${i === 0 ? "M" : "L"} ${(i * stepX).toFixed(1)} ${yFor(p.value).toFixed(1)}`)
                  .join(" ")}
              />
            ))}
            {hoverIndex !== null && (
              <>
                <line
                  x1={hoverIndex * stepX} y1={0} x2={hoverIndex * stepX} y2={height}
                  stroke="var(--border-strong)" strokeWidth={1}
                />
                {series.map((s) => (
                  <circle
                    key={s.label}
                    cx={hoverIndex * stepX}
                    cy={yFor(s.points[hoverIndex].value)}
                    r={3}
                    fill={s.color}
                  />
                ))}
              </>
            )}
          </svg>
          {hoverIndex !== null && hoverPct !== null && (
            <div
              className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-border bg-bg-surface px-2 py-1.5 text-[10px] shadow-lg"
              style={{ left: `${hoverPct}%` }}
            >
              <div className="mb-1 font-mono text-text-muted">
                {formatTime(series[0].points[hoverIndex].timestamp)}
              </div>
              {series.map((s) => (
                <div key={s.label} className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: s.color }} />
                  {series.length > 1 && <span className="text-text-secondary">{s.label}</span>}
                  <span className="font-medium text-text-primary">
                    {formatValue(s.points[hoverIndex].value)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="ml-12 flex justify-between pl-0 font-mono text-[9px] text-text-muted">
        <span>{formatTime(series[0].points[0].timestamp)}</span>
        <span>{formatTime(series[0].points[pointCount - 1].timestamp)}</span>
      </div>
    </div>
  );
}

// Discrete buckets (daily playtime, per-session length) — bars, not a
// line, so the chart doesn't imply continuity between separate buckets
// (game-server-monitor's own bars-vs-line discipline).
export function BarSeries({
  values,
  height = 80,
  color = "var(--accent)",
  mutedIndexes,
}: {
  values: number[];
  height?: number;
  color?: string;
  /** Indexes rendered in a muted tone — e.g. an in-progress session bar. */
  mutedIndexes?: Set<number>;
}) {
  const width = 600;
  const max = Math.max(1, ...values);
  const gap = 2;
  const barWidth = values.length > 0 ? width / values.length - gap : 0;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-20 w-full"
      role="img"
      aria-hidden
    >
      {values.map((v, i) => {
        const barHeight = (v / max) * height;
        const x = i * (barWidth + gap);
        return (
          <rect
            key={i}
            x={x}
            y={height - barHeight}
            width={Math.max(1, barWidth)}
            height={barHeight}
            fill={mutedIndexes?.has(i) ? "var(--border-strong)" : color}
          />
        );
      })}
    </svg>
  );
}

// A 24×7 weekday×hour intensity grid, single-hue (colorblind-safe: hue
// never carries meaning here, only opacity does).
export function HeatmapGrid({
  cells,
  weekdayLabels,
}: {
  cells: { weekday: number; hour: number; value: number }[];
  weekdayLabels: string[];
}) {
  const max = Math.max(1, ...cells.map((c) => c.value));
  const byCell = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c.value]));

  return (
    <div className="overflow-x-auto" role="region" aria-label="activity heatmap">
      <div className="inline-grid grid-cols-[auto_repeat(24,1fr)] gap-[2px]" style={{ minWidth: 640 }}>
        <div />
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="text-center font-mono text-[8px] text-text-muted">
            {h % 3 === 0 ? h : ""}
          </div>
        ))}
        {weekdayLabels.map((label, weekday) => (
          <Fragment key={weekday}>
            <div className="pr-2 text-right font-mono text-[9px] text-text-muted">{label}</div>
            {Array.from({ length: 24 }, (_, hour) => {
              const value = byCell.get(`${weekday}-${hour}`) ?? 0;
              const opacity = value === 0 ? 0.06 : 0.15 + (value / max) * 0.85;
              return (
                <div
                  key={`${weekday}-${hour}`}
                  className="aspect-square rounded-[2px] bg-accent"
                  style={{ opacity }}
                  title={value > 0 ? value.toFixed(1) : undefined}
                />
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
