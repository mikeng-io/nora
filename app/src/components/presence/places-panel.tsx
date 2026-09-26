"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Briefcase, House, Info, MapPin } from "lucide-react";
import type { DerivedPlace } from "@/lib/api/presence";
import { useDerivedPlaces } from "@/lib/queries";
import { formatDate, formatHours, formatNumber } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Panel } from "@/components/ui/panel";
import { Meter } from "@/components/ui/stat";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { personStyle } from "@/lib/person";

const NoraMap = dynamic(() => import("@/components/map/nora-map").then((m) => m.NoraMap), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-hoodie" />,
});

// The places Nora has worked out for one person from where they settle:
// home and work when she is confident, every other regular stop as a
// place of its own (nora/analytics/rollup.py). Drawn as areas at their
// real radius, listed by time spent.
export function PlacesPanel({ subjectId, name }: { subjectId: string; name: string }) {
  const t = useTranslations("patterns");
  const locale = useLocale();
  const derived = useDerivedPlaces();
  const [focus, setFocus] = useState<number | null>(null);

  const places = useMemo(
    () =>
      [...(derived.data?.subjects.find((s) => s.subjectId === subjectId)?.places ?? [])].sort(
        (a, b) => b.totalDwellMs - a.totalDwellMs,
      ),
    [derived.data, subjectId],
  );

  const placeName = (p: DerivedPlace, i: number) =>
    p.label ?? (p.kind === "home" ? t("kind.home") : p.kind === "work" ? t("kind.work") : t("kind.other", { n: i + 1 }));

  if (derived.isPending) return <Skeleton className="h-[420px] rounded-panel" />;
  if (derived.isError && !derived.data) return <UnavailableState onRetry={() => derived.refetch()} />;
  if (places.length === 0) return <EmptyState art="notebook">{t("places.empty", { name })}</EmptyState>;

  const focused = focus !== null ? places[focus] : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <Panel className="relative h-[300px] overflow-hidden sm:h-[380px] lg:h-auto lg:min-h-[440px]">
        <NoraMap
          className="absolute inset-0"
          ariaLabel={t("places.mapLabel", { name })}
          areas={places.map((p, i) => ({
            id: String(i),
            personId: subjectId,
            lat: p.lat,
            lng: p.lng,
            radiusM: p.radiusM,
            label: placeName(p, i),
            emphasis: focus === i,
          }))}
          frameKey={focused ? `place-${focus}` : "all"}
          fitTo={
            focused
              ? [[focused.lng, focused.lat]]
              : places.map((p) => [p.lng, p.lat] as [number, number])
          }
        />
      </Panel>
      <Panel>
        <ul className="divide-y divide-hairline" style={personStyle(subjectId)}>
          {places.map((p, i) => {
            const Icon = p.kind === "home" ? House : p.kind === "work" ? Briefcase : MapPin;
            return (
              <li key={i}>
                <button
                  type="button"
                  onClick={() => setFocus(focus === i ? null : i)}
                  aria-pressed={focus === i}
                  className={cn(
                    "grid w-full gap-2.5 px-4 py-3.5 text-left transition-colors duration-100 hover:bg-hoodie/60 sm:px-5",
                    focus === i && "bg-hoodie/60",
                  )}
                >
                  <span className="flex items-center gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-control bg-(--person-wash) text-(--person)">
                      <Icon className="size-4" aria-hidden="true" />
                    </span>
                    <span className="grid min-w-0 flex-1">
                      <span className="truncate text-[0.9375rem] font-semibold text-ink">{placeName(p, i)}</span>
                      <span className="truncate text-meta text-ink-muted tnum">
                        {t("places.summary", {
                          stays: formatNumber(p.stayCount, locale),
                          dwell: formatHours(p.totalDwellMs, locale),
                        })}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-label text-ink-muted">
                      {t("places.last", { date: formatDate(p.lastSeenTs, locale) })}
                    </span>
                  </span>
                  {p.kind === "home" || p.kind === "work" ? (
                    <span className="flex items-center gap-2.5 pl-12">
                      <Meter
                        value={p.confidence}
                        label={t("places.confidence")}
                        tone={p.confidence >= 0.7 ? "denim" : "ink"}
                        className="flex-1"
                      />
                      <span className="w-9 text-right font-mono text-label text-ink-soft tnum">
                        {Math.round(p.confidence * 100)}%
                      </span>
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="flex items-start gap-2 border-t border-hairline px-4 py-3 text-label text-ink-muted sm:px-5">
          <Tooltip content={t("places.confidenceHelp")}>
            <button type="button" className="mt-px shrink-0 rounded-full" aria-label={t("places.confidence")}>
              <Info className="size-3.5" aria-hidden="true" />
            </button>
          </Tooltip>
          {t("places.note")}
        </p>
      </Panel>
    </div>
  );
}
