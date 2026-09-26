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
  tableLabel,
  hideTableLabel,
}: {
  cells: HeatCell[];
  dayLabels: string[];
  formatValue: (v: number) => string;
  label: string;
  now?: { day: number; hour: number } | null;
  className?: string;
  /** "Show as a table" / "Hide the table": every chart has a table view. */
  tableLabel?: string;
  hideTableLabel?: string;
}) {
  const [active, setActive] = useState<HeatCell | null>(null);
  const [table, setTable] = useState(false);
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
      {table ? (
        <div className="scroll-thin overflow-x-auto rounded-control border border-hairline">
          <table className="w-full border-collapse font-mono text-[0.6875rem] text-ink tnum">
            <caption className="sr-only">{label}</caption>
            <thead>
              <tr>
                <th scope="col" className="sticky left-0 bg-hoodie px-2 py-1.5 text-left font-medium text-ink-muted" />
                {Array.from({ length: 24 }, (_, h) => (
                  <th key={h} scope="col" className="bg-hoodie px-1.5 py-1.5 font-medium text-ink-muted">
                    {String(h).padStart(2, "0")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.map((row, day) => (
                <tr key={day} className="border-t border-hairline">
                  <th scope="row" className="sticky left-0 bg-surface px-2 py-1 text-left font-sans font-medium text-ink-soft">
                    {dayLabels[day]}
                  </th>
                  {row.map((v, hour) => (
                    <td key={hour} className="px-1.5 py-1 text-right whitespace-nowrap">
                      {v > 0 ? formatValue(v) : ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="flex min-h-5 flex-wrap items-center justify-between gap-3 text-label text-ink-muted">
        <span aria-live="polite" className="font-mono tnum">
          {active
            ? `${dayLabels[active.day]} ${String(active.hour).padStart(2, "0")}:00 · ${formatValue(active.value)}`
            : " "}
        </span>
        <span className="flex items-center gap-3">
          {tableLabel ? (
            <button
              type="button"
              onClick={() => setTable((t) => !t)}
              aria-expanded={table}
              className="rounded-control px-1.5 py-1 font-sans text-label font-semibold text-denim hover:bg-hoodie"
            >
              {table ? (hideTableLabel ?? tableLabel) : tableLabel}
            </button>
          ) : null}
          <span className="flex items-center gap-1" aria-hidden="true">
            {Array.from({ length: STEPS + 1 }, (_, s) => (
              <span key={s} className="size-2.5 rounded-[2px]" style={{ background: fill(s) }} />
            ))}
          </span>
        </span>
      </div>
    </div>
  );
}
