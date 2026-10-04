"use client";

import { useContext, useEffect, useId, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Meter } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { DocumentIdContext } from "./blocks";
import { checklistKey, deviceStorage, readTicks, writeTicks } from "./checklist-state";
import type { Render } from "./types";

// Things to prepare, ticked off by the reader. The ticks live in this browser only
// (checklist-state.ts), are read in an effect so the server and the first client render
// agree, and never gate the page: with storage blocked, ticking still works in memory.
export function ChecklistBlock({ props }: Render<"Checklist">) {
  const t = useTranslations("documents.renderer");
  const documentId = useContext(DocumentIdContext);
  const headingId = useId();
  const items = props.items;
  const key = useMemo(
    () => checklistKey(documentId, props.title, items.map((i) => i.text)),
    [documentId, props.title, items],
  );
  const [ticked, setTicked] = useState<ReadonlySet<number>>(() => new Set());
  // What a screen reader is told after a tick. Empty until the reader acts, so neither the
  // first paint nor the stored ticks arriving read anything out.
  const [announce, setAnnounce] = useState("");

  useEffect(() => {
    setTicked(new Set(readTicks(deviceStorage(), key, items.length)));
    setAnnounce("");
  }, [key, items.length]);

  const commit = (next: Set<number>) => {
    setTicked(next);
    writeTicks(deviceStorage(), key, [...next]);
    setAnnounce(t("checklistDone", { done: next.size, total: items.length }));
  };
  const toggle = (i: number) => {
    const next = new Set(ticked);
    if (!next.delete(i)) next.add(i);
    commit(next);
  };

  const done = ticked.size;
  return (
    <section aria-labelledby={props.title ? headingId : undefined} className="flex min-w-0 flex-col gap-3 rounded-panel border border-hairline bg-surface px-4 py-3">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {props.title ? (
            <h3 id={headingId} className="min-w-0 text-title font-semibold text-ink [overflow-wrap:anywhere]">
              {props.title}
            </h3>
          ) : null}
          <p className="text-meta text-ink-muted tnum">{t("checklistDone", { done, total: items.length })}</p>
        </div>
        <span className="sr-only" aria-live="polite" role="status">
          {announce}
        </span>
        <Meter value={done} max={items.length} label={t("checklistProgress")} tone="success" />
      </div>
      <ul className="flex flex-col divide-y divide-hairline">
        {items.map((it, i) => {
          const on = ticked.has(i);
          return (
            <li key={i}>
              <label className="flex min-h-11 cursor-pointer items-start gap-3 py-2.5">
                <span className="relative mt-0.5 flex size-5 shrink-0">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(i)}
                    className="peer size-5 cursor-pointer appearance-none rounded-[6px] border border-seam bg-surface transition-colors duration-100 checked:border-denim checked:bg-denim focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-denim motion-reduce:transition-none print:checked:border-ink print:checked:bg-transparent"
                  />
                  <Check aria-hidden strokeWidth={3} className="pointer-events-none absolute inset-0 m-auto size-3.5 text-surface opacity-0 peer-checked:opacity-100 print:text-ink" />
                </span>
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className={cn("text-body [overflow-wrap:anywhere]", on ? "text-ink-muted line-through" : "text-ink")}>{it.text}</span>
                  {it.note ? <span className="text-meta text-ink-muted [overflow-wrap:anywhere]">{it.note}</span> : null}
                </span>
                {it.owner ? <Badge className="max-w-[40%] shrink-0">{it.owner}</Badge> : null}
              </label>
            </li>
          );
        })}
      </ul>
      {done > 0 ? (
        <div className="flex" data-print-hide>
          <button
            type="button"
            onClick={() => commit(new Set())}
            className="inline-flex min-h-9 items-center rounded-control px-2 text-meta text-ink-soft underline underline-offset-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-denim"
          >
            {t("checklistClear")}
          </button>
        </div>
      ) : null}
    </section>
  );
}
