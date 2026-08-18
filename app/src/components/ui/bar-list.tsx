"use client";

import { useState } from "react";
import { Link } from "@/i18n/routing";
import { cn } from "@/lib/utils";

// A ranked list + proportional bar, the one shape behind ~6 different
// panels on Pulse's own web app (skills, kill counts, stockpile, death
// causes, coplay, leaderboards) — one generic component instead of one
// bespoke list per panel. Bars scale against the FULL dataset's max, not
// just the visible/expanded slice, so a bar's length never jumps when the
// list expands (matches game-server-monitor's own BarList reasoning).

export interface BarListItem {
  key: string;
  label: string;
  value: number;
  valueLabel: string;
  href?: string;
  /** Rendered dimmed/monospace instead of trusting `label` as a real name
   * — for an id Pulse couldn't resolve to a display name. */
  unknown?: boolean;
}

export function BarList({
  items,
  collapsedCount = 8,
  emptyLabel,
  showMoreLabel,
  showLessLabel,
}: {
  items: BarListItem[];
  collapsedCount?: number;
  emptyLabel: string;
  showMoreLabel: (count: number) => string;
  showLessLabel: string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (items.length === 0) {
    return <p className="font-mono text-[12.5px] text-text-muted">{emptyLabel}</p>;
  }

  const max = Math.max(1, ...items.map((i) => i.value));
  const visible = expanded ? items : items.slice(0, collapsedCount);
  const hiddenCount = items.length - collapsedCount;

  return (
    <div>
      <ul className="space-y-1.5">
        {visible.map((item) => (
          <li key={item.key} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="relative overflow-hidden rounded bg-bg-elevated">
              <div
                className="absolute inset-y-0 left-0 bg-accent/25"
                style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }}
                aria-hidden
              />
              <div className="relative flex items-center px-2.5 py-1.5 text-[12.5px]">
                {item.href ? (
                  <Link href={item.href} className="truncate text-text-primary hover:underline">
                    {item.label}
                  </Link>
                ) : (
                  <span
                    className={cn(
                      "truncate",
                      item.unknown ? "font-mono text-text-muted" : "text-text-primary",
                    )}
                  >
                    {item.label}
                  </span>
                )}
              </div>
            </div>
            <span className="whitespace-nowrap font-mono text-[11px] text-text-muted">
              {item.valueLabel}
            </span>
          </li>
        ))}
      </ul>
      {items.length > collapsedCount && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-2 font-mono text-[10px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
        >
          {expanded ? showLessLabel : showMoreLabel(hiddenCount)}
        </button>
      )}
    </div>
  );
}
