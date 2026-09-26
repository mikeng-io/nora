"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useWidth } from "./use-width";

// Horizontal bars for a ranked list (a leaderboard, causes of death, a
// stockpile): the label and value are text, the bar is the only ink. One
// series, one colour (denim); a row may carry its own leading mark (an
// avatar) so identity never rests on the bar's colour.

export interface BarDatum {
  key: string;
  label: ReactNode;
  value: number;
  display: string;
  lead?: ReactNode;
  hint?: ReactNode;
  href?: string;
  muted?: boolean;
}

export function BarList({
  data,
  label,
  className,
  max: maxOverride,
}: {
  data: BarDatum[];
  label: string;
  className?: string;
  max?: number;
}) {
  const max = maxOverride ?? Math.max(0, ...data.map((d) => d.value));
  return (
    <ol aria-label={label} className={cn("grid gap-2.5", className)}>
      {data.map((d) => (
        <li key={d.key} className="grid gap-1.5">
          <div className="flex items-center gap-2.5">
            {d.lead}
            <span className={cn("min-w-0 flex-1 truncate text-body", d.muted ? "text-ink-muted" : "text-ink")}>
              {d.label}
            </span>
            {d.hint ? <span className="hidden text-meta text-ink-muted sm:inline">{d.hint}</span> : null}
            <span className="shrink-0 font-mono text-meta font-medium text-ink tnum">{d.display}</span>
          </div>
          <span className="block h-1.5 overflow-hidden rounded-full bg-hoodie" aria-hidden="true">
            <span
              className="block h-full origin-left rounded-full bg-denim transition-transform duration-320 ease-nora"
              style={{ transform: `scaleX(${max > 0 ? Math.max(0.015, d.value / max) : 0})` }}
            />
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Columns over a sequence (playtime per day, session lengths): 4px rounded
 * tops, square at the baseline, capped at 24px wide with air between. */
export function Columns({
  data,
  height = 120,
  label,
  className,
}: {
  data: Array<{ key: string; value: number; tick?: string; title: string }>;
  height?: number;
  label: string;
  className?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const max = Math.max(0, ...data.map((d) => d.value));
  const slot = data.length ? width / data.length : 0;
  const bar = Math.max(3, Math.min(24, slot - 4));
  return (
    <div ref={ref} className={cn("grid gap-1.5", className)}>
      <svg width={width} height={height} role="img" aria-label={label} className="block overflow-visible">
        <line x1={0} x2={width} y1={height - 0.5} y2={height - 0.5} stroke="var(--hairline)" />
        {data.map((d, i) => {
          const hgt = max > 0 ? Math.max(d.value > 0 ? 2 : 0, (d.value / max) * (height - 4)) : 0;
          const x = i * slot + (slot - bar) / 2;
          const r = Math.min(4, bar / 2, hgt);
          const y = height - hgt;
          return (
            <path
              key={d.key}
              d={`M${x},${height}V${y + r}Q${x},${y} ${x + r},${y}H${x + bar - r}Q${x + bar},${y} ${x + bar},${y + r}V${height}Z`}
              className="fill-denim transition-opacity duration-100 hover:opacity-80"
            >
              <title>{d.title}</title>
            </path>
          );
        })}
      </svg>
      {data.some((d) => d.tick) ? (
        <div className="grid font-mono text-[0.625rem] text-ink-muted tnum" style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)` }}>
          {data.map((d) => (
            <span key={d.key} className="truncate text-center">
              {d.tick ?? ""}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
