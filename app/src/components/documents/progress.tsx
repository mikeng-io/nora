"use client";

import { useFormatter } from "next-intl";
import { Panel } from "@/components/ui/panel";
import { Meter } from "@/components/ui/stat";
import type { Render } from "./types";

const METER = { neutral: "ink", info: "denim", success: "success", warning: "caution", danger: "danger" } as const;

/** How far a count has got toward its target. Over the target reads as full, and the
 * percent only reaches 100 when the count does. */
export function ProgressBlock({ props }: Render<"Progress">) {
  const f = useFormatter();
  const value = Math.min(Math.max(props.value, 0), props.max);
  const pct = value >= props.max ? 1 : Math.floor((value / props.max) * 100) / 100;
  const n = (v: number) => f.number(v, { maximumFractionDigits: 2 });

  return (
    <Panel className="flex flex-col gap-2 px-4 py-3 sm:px-5 sm:py-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 text-title font-semibold break-words text-ink">{props.label}</p>
        <p className="shrink-0 font-mono text-meta tnum text-ink-soft">
          {n(props.value)} / {n(props.max)}
          <span className="text-ink-muted">{` · ${f.number(pct, { style: "percent", maximumFractionDigits: 0 })}`}</span>
        </p>
      </div>
      <Meter value={value} max={props.max} label={props.label} tone={METER[props.tone]} />
      {props.detail ? <p className="text-meta text-ink-muted">{props.detail}</p> : null}
    </Panel>
  );
}
