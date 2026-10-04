"use client";

import { useTranslations } from "next-intl";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { Md } from "./markdown";
import type { Render } from "./types";

// A key moment is marked by more than colour: a filled dot on the rail and a bar on the
// title's left edge, plus a word for a screen reader.
const DOT = {
  neutral: "border-ink-muted bg-surface",
  info: "border-denim bg-denim",
  success: "border-success bg-success",
  warning: "border-caution bg-caution",
  danger: "border-danger bg-danger",
} as const;
const EDGE = {
  neutral: "border-l-transparent",
  info: "border-l-denim",
  success: "border-l-success",
  warning: "border-l-caution",
  danger: "border-l-danger",
} as const;
const TONE_KEY = { info: "toneInfo", success: "toneSuccess", warning: "toneWarning", danger: "toneDanger" } as const;

// One grid per entry: rail | content on a phone (label above the title), label | rail |
// content from `sm`. Every cell sits in the same row from `sm` up, so the rail runs the
// whole height of the entry and the next entry's rail continues it.
export function TimelineBlock({ props }: Render<"Timeline">) {
  const t = useTranslations("documents.renderer");
  const last = props.items.length - 1;
  return (
    <ol className="flex min-w-0 flex-col">
      {props.items.map((it, i) => {
        const tone = it.tone ?? "neutral";
        return (
          <li key={i} className="grid min-w-0 grid-cols-[1.25rem_minmax(0,1fr)] gap-x-3 sm:grid-cols-[8rem_1.25rem_minmax(0,1fr)] sm:gap-x-4">
            <span aria-hidden className="relative col-start-1 row-span-2 row-start-1 sm:col-start-2 sm:row-span-1">
              {/* The line: from the dot down to the next entry's, not above the first or below the last. */}
              {last > 0 ? (
                <span className={cn("absolute left-1/2 w-px -translate-x-1/2 bg-hairline", i === 0 ? "top-3 bottom-0" : i === last ? "top-0 h-3" : "inset-y-0")} />
              ) : null}
              <span className={cn("absolute top-1.5 left-1/2 size-3 -translate-x-1/2 rounded-full border-2", DOT[tone])} />
            </span>
            <p className="col-start-2 row-start-1 justify-self-start pb-1.5 sm:col-start-1 sm:justify-self-end sm:pb-0">
              <span className="inline-block max-w-full rounded-full bg-hoodie px-2.5 py-0.5 font-mono text-meta text-ink-soft tnum [overflow-wrap:anywhere]">
                {it.label}
              </span>
            </p>
            <div className={cn("col-start-2 row-start-2 flex min-w-0 flex-col gap-2 sm:col-start-3 sm:row-start-1", i < last && "pb-6")}>
              <p className={cn("border-l-2 pl-2 text-title font-semibold text-ink [overflow-wrap:anywhere]", EDGE[tone])}>
                {tone !== "neutral" ? <span className="sr-only">{t(TONE_KEY[tone])}: </span> : null}
                {it.title}
              </p>
              {it.text ? (
                <div className="min-w-0 [overflow-wrap:anywhere]">
                  <Md text={it.text} />
                </div>
              ) : null}
              {it.people ? (
                <ul className="flex flex-wrap gap-1.5">
                  {it.people.map((p, j) => (
                    <li key={j} className="flex min-w-0 max-w-full">
                      <Badge>{p}</Badge>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
