"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";

// A week as a grid: seven rows of days, twenty-four columns of hours, each
// cell a step of one sequential ramp (hoodie to denim: magnitude is one
// hue, light to dark). Hover or focus a cell for its value; the current
// hour is outlined when `now` is given. The whole grid reads as a sentence
// to assistive tech, and the busiest hours are named in text beside it.

export interface HeatCell {
  day: number; // 0..6, in the order of `dayLabels`
  hour: number; // 0..23
  value: number;
}

const STEPS = 5;

export function WeekHeatmap({
  cells,
  dayLabels,
  formatValue,
  label,
  now,
  className,
}: {
  cells: HeatCell[];
  dayLabels: string[];
  formatValue: (v: number) => string;
  label: string;
  now?: { day: number; hour: number } | null;
  className?: string;
}) {
  const [active, setActive] = useState<HeatCell | null>(null);
  const grid = useMemo(() => {
    const g: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0));
    for (const c of cells) if (c.day >= 0 && c.day < 7 && c.hour >= 0 && c.hour < 24) g[c.day][c.hour] += c.value;
    return g;
  }, [cells]);
  const max = Math.max(0, ...grid.flat());

  const step = (v: number) => (max <= 0 || v <= 0 ? 0 : Math.max(1, Math.ceil((v / max) * STEPS)));
  const fill = (s: number) =>
    s === 0 ? "var(--hoodie)" : `color-mix(in oklab, var(--denim) ${[0, 22, 40, 58, 78, 100][s]}%, var(--hoodie))`;

  return (
    <div className={cn("grid gap-3", className)}>
      <div role="img" aria-label={label} className="relative">
        <div className="grid grid-cols-[2.25rem_1fr] gap-x-2 gap-y-[3px] sm:grid-cols-[2.75rem_1fr]">
          {grid.map((row, day) => (
            <div key={day} className="contents">
              <span className="self-center text-label text-ink-muted">{dayLabels[day]}</span>
              <div className="grid grid-cols-24 gap-[2px]">
                {row.map((v, hour) => {
                  const isNow = now?.day === day && now?.hour === hour;
                  return (
                    <span
                      key={hour}
                      tabIndex={-1}
                      onPointerEnter={() => setActive({ day, hour, value: v })}
                      onPointerLeave={() => setActive(null)}
                      className={cn(
                        "h-3.5 rounded-[3px] transition-[box-shadow] duration-100 sm:h-4 lg:h-5",
                        isNow && "ring-2 ring-ink ring-offset-1 ring-offset-surface",
                        active?.day === day && active.hour === hour && "ring-2 ring-denim ring-offset-1 ring-offset-surface",
                      )}
                      style={{ background: fill(step(v)) }}
                    />
                  );
                })}
              </div>
            </div>
          ))}
          <span />
          <div className="mt-1 grid grid-cols-24 gap-[2px] font-mono text-[0.625rem] text-ink-muted tnum">
            {Array.from({ length: 24 }, (_, h) => (
              <span key={h} className="text-center">
                {h % 6 === 0 ? String(h).padStart(2, "0") : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="flex min-h-5 flex-wrap items-center justify-between gap-3 text-label text-ink-muted">
        <span aria-live="polite" className="font-mono tnum">
          {active
            ? `${dayLabels[active.day]} ${String(active.hour).padStart(2, "0")}:00 · ${formatValue(active.value)}`
            : " "}
        </span>
        <span className="flex items-center gap-1" aria-hidden="true">
          {Array.from({ length: STEPS + 1 }, (_, s) => (
            <span key={s} className="size-2.5 rounded-[2px]" style={{ background: fill(s) }} />
          ))}
        </span>
      </div>
    </div>
  );
}
