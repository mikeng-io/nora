"use client";

// A rota or run of show: one row per person or team on a shared time axis. Two views from
// one layout (schedule-layout.ts): from `sm` up a grid that scrolls sideways under a
// sticky lane column, on a phone a plain list per lane (a Gantt cannot be read at 390px),
// and in print the grid, fitted to the page width instead of scrolled.
import { useTranslations } from "next-intl";
import { cn } from "@/lib/cn";
import { axisWidth, layoutSchedule, type LaidBlock, type Tone } from "./schedule-layout";
import type { Render } from "./types";

/** Height of one lane row in the grid. Room for a three-line title and the time. */
const ROW_H = 92;
/** A block this wide (minutes) has room for its note under the title. */
const NOTE_MIN = 90;

const WASH: Record<Tone, string> = {
  neutral: "border-l-seam bg-hoodie",
  info: "border-l-denim bg-denim/10",
  success: "border-l-success bg-success/10",
  warning: "border-l-caution bg-caution/10",
  danger: "border-l-danger bg-danger/10",
};
const BAR: Record<Tone, string> = {
  neutral: "border-l-seam",
  info: "border-l-denim",
  success: "border-l-success",
  warning: "border-l-caution",
  danger: "border-l-danger",
};

const range = (b: LaidBlock) => `${b.from}–${b.to}`;
/** The native tooltip: everything a block says, for when it is too small to show it. */
const full = (b: LaidBlock) => [`${range(b)} ${b.title}`, b.note].filter(Boolean).join("\n");

export function ScheduleBlock({ props }: Render<"Schedule">) {
  const t = useTranslations("documents.renderer");
  const layout = layoutSchedule(props);
  const cols = "grid-cols-[8rem_var(--sched-w)] print:grid-cols-[8rem_minmax(0,1fr)]";
  const empty = <p className="px-3 py-2 text-meta text-ink-muted">{t("scheduleEmptyLane")}</p>;

  return (
    <figure className="flex min-w-0 flex-col gap-2">
      <figcaption className="text-meta text-ink-muted">{props.caption}</figcaption>

      {/* The grid. A wide axis scrolls, so the region takes focus (axe:
          scrollable-region-focusable) and has a name. */}
      <div
        role="region"
        aria-label={props.caption}
        tabIndex={0}
        className="hidden overflow-x-auto rounded-panel border border-hairline bg-surface focus-visible:outline-2 focus-visible:outline-denim sm:block print:block"
      >
        <div
          className="flex w-max flex-col print:w-full"
          style={{ "--sched-w": `${axisWidth(layout.span)}px` } as React.CSSProperties}
        >
          <div className={cn("grid border-b border-hairline", cols)} aria-hidden>
            <div className="sticky left-0 z-10 border-r border-hairline bg-surface" />
            <div className="relative h-7">
              {layout.ticks.map((tk) => (
                <span
                  key={tk.min}
                  className={cn("absolute bottom-0 w-px bg-hairline", tk.hour ? "top-0" : "top-4 opacity-50")}
                  style={{ left: `${tk.left}%` }}
                />
              ))}
              {layout.ticks.map((tk) =>
                tk.label ? (
                  <span key={tk.min} className="absolute top-1 pl-1.5 font-mono text-label tnum text-ink-muted" style={{ left: `${tk.left}%` }}>
                    {tk.label}
                  </span>
                ) : null,
              )}
            </div>
          </div>

          {layout.lanes.map((lane, li) => (
            <div key={li} role="group" aria-label={lane.name} className={cn("grid", cols, li > 0 && "border-t border-hairline")}>
              <p className="sticky left-0 z-10 min-w-0 border-r border-hairline bg-surface px-3 py-3 text-label font-semibold break-words text-ink">
                {lane.name}
              </p>
              <div className="relative" style={{ height: lane.rows * ROW_H }}>
                {layout.ticks.map((tk) => (
                  <span
                    key={tk.min}
                    aria-hidden
                    className={cn("absolute inset-y-0 w-px bg-hairline", !tk.hour && "opacity-50")}
                    style={{ left: `${tk.left}%` }}
                  />
                ))}
                {lane.blocks.length === 0
                  ? empty
                  : lane.blocks.map((b, bi) => {
                      const withNote = Boolean(b.note) && b.toMin - b.fromMin >= NOTE_MIN;
                      return (
                        <div
                          key={bi}
                          title={full(b)}
                          className={cn(
                            "absolute flex min-w-0 flex-col gap-0.5 overflow-hidden rounded-control border-l-4 px-2 py-1.5",
                            WASH[b.tone],
                          )}
                          style={{ left: `calc(${b.left}% + 1px)`, width: `calc(${b.width}% - 2px)`, top: b.row * ROW_H + 4, height: ROW_H - 8 }}
                        >
                          <p className={cn("text-label font-semibold break-words text-ink", withNote ? "line-clamp-2" : "line-clamp-3")}>{b.title}</p>
                          <p className="font-mono text-label tnum text-ink-soft">{range(b)}</p>
                          {withNote ? <p className="line-clamp-1 text-label text-ink-soft">{b.note}</p> : null}
                        </div>
                      );
                    })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* The phone: a heading per lane and its blocks in time order, the note always shown. */}
      <div className="flex flex-col divide-y divide-hairline rounded-panel border border-hairline bg-surface sm:hidden print:hidden">
        {layout.lanes.map((lane, li) => (
          <section key={li} className="flex flex-col gap-2 px-4 py-3">
            <h3 className="text-title font-semibold break-words text-ink">{lane.name}</h3>
            {lane.blocks.length === 0 ? (
              <p className="text-meta text-ink-muted">{t("scheduleEmptyLane")}</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {lane.blocks.map((b, bi) => (
                  <li key={bi} className={cn("flex min-w-0 flex-col gap-0.5 border-l-4 pl-3", BAR[b.tone])}>
                    <p className="font-mono text-meta tnum text-ink-soft">{range(b)}</p>
                    <p className="text-body font-semibold break-words text-ink">{b.title}</p>
                    {b.note ? <p className="text-meta break-words text-ink-soft">{b.note}</p> : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </figure>
  );
}
