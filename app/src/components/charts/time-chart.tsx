"use client";

import { useId, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { niceTicks, useWidth } from "./use-width";

// One measure over time: a 2px line with a 10% wash under it, hairline
// gridlines, one y-axis. Hover, drag or arrow-key along it for a crosshair
// and the exact value. A single series is denim and needs no legend (the
// panel's title names it). Two measures of different scale are two charts,
// never a second axis.

export interface TimePoint {
  t: number;
  v: number;
}

export function TimeChart({
  points,
  height = 168,
  formatValue,
  formatTime,
  formatTick,
  label,
  yMax,
  step = false,
  className,
}: {
  points: TimePoint[];
  height?: number;
  formatValue: (v: number) => string;
  /** The crosshair's time label. */
  formatTime: (t: number) => string;
  /** The x-axis ticks. */
  formatTick: (t: number) => string;
  label: string;
  /** A fixed ceiling (100 for a percentage), else the data's own. */
  yMax?: number;
  /** Draw as steps (a count that changes discretely, like players online). */
  step?: boolean;
  className?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const gradId = useId();
  const pad = { top: 8, right: 8, bottom: 22, left: 36 };
  const w = Math.max(0, width - pad.left - pad.right);
  const h = height - pad.top - pad.bottom;

  const { path, area, ticksY, ticksX, x, y, t0, t1 } = useMemo(() => {
    const t0 = points[0]?.t ?? 0;
    const t1 = points[points.length - 1]?.t ?? 1;
    const vmax = yMax ?? Math.max(1, ...points.map((p) => p.v));
    const ticksY = niceTicks(vmax, 3);
    const top = ticksY[ticksY.length - 1] || 1;
    const x = (t: number) => (t1 === t0 ? w / 2 : ((t - t0) / (t1 - t0)) * w);
    const y = (v: number) => h - (v / top) * h;
    let path = "";
    points.forEach((p, i) => {
      const px = x(p.t).toFixed(1);
      const py = y(p.v).toFixed(1);
      if (i === 0) path += `M${px},${py}`;
      else if (step) path += `H${px}V${py}`;
      else path += `L${px},${py}`;
    });
    const area = points.length ? `${path}V${h}H${x(t0).toFixed(1)}Z` : "";
    const span = t1 - t0;
    const n = width < 420 ? 3 : 5;
    const ticksX = Array.from({ length: n }, (_, i) => t0 + (span * i) / (n - 1));
    return { path, area, ticksY, ticksX, x, y, t0, t1 };
  }, [points, w, h, yMax, step, width]);

  const nearest = (px: number) => {
    if (!points.length) return null;
    const t = t0 + (px / Math.max(1, w)) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].t - t) < Math.abs(points[best].t - t)) best = i;
    return best;
  };

  const hp = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} className={cn("relative select-none", className)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label}
        tabIndex={0}
        className="block touch-pan-y overflow-visible focus-visible:outline-offset-4"
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          setHover(nearest(e.clientX - r.left - pad.left));
        }}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (!points.length) return;
          if (e.key === "ArrowRight") setHover((i) => Math.min(points.length - 1, (i ?? -1) + 1));
          else if (e.key === "ArrowLeft") setHover((i) => Math.max(0, (i ?? points.length) - 1));
          else if (e.key === "Escape") setHover(null);
        }}
        onBlur={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="var(--denim)" stopOpacity="0.12" />
            <stop offset="1" stopColor="var(--denim)" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        <g transform={`translate(${pad.left},${pad.top})`}>
          {ticksY.map((tv) => (
            <g key={tv}>
              <line x1={0} x2={w} y1={y(tv)} y2={y(tv)} stroke="var(--hairline)" strokeWidth={1} />
              <text
                x={-8}
                y={y(tv)}
                dy="0.32em"
                textAnchor="end"
                className="fill-ink-muted font-mono text-[10px] tnum"
              >
                {formatValue(tv)}
              </text>
            </g>
          ))}
          {ticksX.map((tx, i) => (
            <text
              key={i}
              x={x(tx)}
              y={h + 16}
              textAnchor={i === 0 ? "start" : i === ticksX.length - 1 ? "end" : "middle"}
              className="fill-ink-muted font-mono text-[10px] tnum"
            >
              {formatTick(tx)}
            </text>
          ))}
          {points.length > 1 ? (
            <>
              <path d={area} fill={`url(#${gradId})`} />
              <path d={path} fill="none" stroke="var(--denim)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            </>
          ) : null}
          {hp ? (
            <g pointerEvents="none">
              <line x1={x(hp.t)} x2={x(hp.t)} y1={0} y2={h} stroke="var(--seam)" strokeWidth={1} />
              <circle cx={x(hp.t)} cy={y(hp.v)} r={4.5} fill="var(--denim)" stroke="var(--surface)" strokeWidth={2} />
            </g>
          ) : null}
        </g>
      </svg>
      {/* What the crosshair shows, read out as it moves by keyboard. */}
      <span className="sr-only" aria-live="polite">
        {hp ? `${formatTime(hp.t)}: ${formatValue(hp.v)}` : ""}
      </span>
      {hp ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute top-0 z-10 rounded-control border border-hairline bg-surface px-2.5 py-1.5 shadow-float"
          style={{
            left: Math.min(Math.max(pad.left + x(hp.t) - 60, 0), Math.max(0, width - 124)),
          }}
        >
          <p className="font-mono text-[0.8125rem] font-medium text-ink tnum">{formatValue(hp.v)}</p>
          <p className="font-mono text-[0.6875rem] text-ink-muted tnum">{formatTime(hp.t)}</p>
        </div>
      ) : null}
    </div>
  );
}
