"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ArrowRight, Info } from "lucide-react";
import type { Corridor, Trip } from "@/lib/api/presence";
import { useHistory, useRoutes } from "@/lib/queries";
import {
  dayIndex,
  formatDistance,
  formatDuration,
  formatNumber,
  formatRelative,
  formatTime,
  formatWeekdayDate,
  shortAddress,
} from "@/lib/format";
import { cn } from "@/lib/cn";
import { useNow } from "@/hooks/use-now";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { Badge } from "@/components/ui/badge";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat, StatGrid } from "@/components/ui/stat";
import { Tooltip } from "@/components/ui/tooltip";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { personStyle } from "@/lib/person";

const NoraMap = dynamic(() => import("@/components/map/nora-map").then((m) => m.NoraMap), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-hoodie" />,
});

const WINDOWS = [7, 30, 90] as const;

/** A corridor's name from the ends of its address sequence ("Nathan Road →
 * Harbour City"). Corridors are directional, so from/to is honest. */
export function corridorName(c: Pick<Corridor, "sequence">): { from: string; to: string } | null {
  const from = shortAddress(c.sequence[0]);
  const to = shortAddress(c.sequence[c.sequence.length - 1]);
  return from && to ? { from, to } : null;
}

// Where one person goes: every trip in the window, grouped into corridors
// (the same road sequence taken again), with the chosen trip drawn on the
// map from the positions recorded while it happened.
export function RoutesPanel({ subjectId, name }: { subjectId: string; name: string }) {
  const t = useTranslations("patterns");
  const locale = useLocale();
  const now = useNow();
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [corridorId, setCorridorId] = useState<number | null>(null);
  const [tripId, setTripId] = useState<number | null>(null);
  const routes = useRoutes(subjectId, days);

  const trips = useMemo(
    () =>
      [...(routes.data?.trips ?? [])]
        .filter((tr) => corridorId === null || tr.corridorId === corridorId)
        .sort((a, b) => b.startedTs - a.startedTs),
    [routes.data, corridorId],
  );
  const corridors = useMemo(
    () => [...(routes.data?.corridors ?? [])].sort((a, b) => b.sampleCount - a.sampleCount),
    [routes.data],
  );
  const trip = trips.find((tr) => tr.id === tripId) ?? trips[0] ?? null;

  // The positions behind the chosen trip: one history read wide enough to
  // reach back to it (capped at the backend's 30 days).
  const hoursBack = trip ? Math.min(720, Math.ceil(((now || Date.now()) - trip.startedTs) / 3_600_000) + 1) : 0;
  const history = useHistory(trip ? subjectId : null, Math.max(1, hoursBack));
  const tripPath = useMemo<Array<[number, number]>>(
    () =>
      trip && history.data
        ? history.data.points
            .filter((p) => p.ts >= trip.startedTs && p.ts <= trip.endedTs)
            .map((p) => [p.lng, p.lat])
        : [],
    [trip, history.data],
  );

  const all = routes.data?.trips ?? [];
  const totalDistance = all.reduce((s, tr) => s + tr.distanceM, 0);
  const linked = all.filter((tr) => tr.corridorId !== null).length;

  // Trips grouped by the day they started, newest first.
  const byDay = useMemo(() => {
    const groups: Array<{ day: number; ts: number; trips: Trip[] }> = [];
    for (const tr of trips) {
      const d = dayIndex(tr.startedTs);
      const g = groups[groups.length - 1];
      if (g && g.day === d) g.trips.push(tr);
      else groups.push({ day: d, ts: tr.startedTs, trips: [tr] });
    }
    return groups;
  }, [trips]);

  const header = (
    <PanelHeader
      title={t("routes.title")}
      description={t("routes.lede", { name })}
      action={
        <Segmented
          size="sm"
          label={t("window")}
          value={days}
          onChange={(d) => {
            setDays(d);
            setTripId(null);
            setCorridorId(null);
          }}
          options={WINDOWS.map((d) => ({ value: d, label: t("days", { count: d }) }))}
        />
      }
    />
  );

  if (routes.isPending) return <Skeleton className="h-[480px] rounded-panel" />;
  if (routes.isError && !routes.data) {
    return (
      <Panel>
        {header}
        <UnavailableState compact onRetry={() => routes.refetch()} />
      </Panel>
    );
  }
  if (all.length === 0) {
    return (
      <Panel>
        {header}
        <EmptyState compact art="groupChat">
          {t("routes.empty", { name, days })}
        </EmptyState>
      </Panel>
    );
  }

  return (
    <div className="grid gap-4" style={personStyle(subjectId)}>
      <Panel>
        {header}
        <div className="px-4 pb-5 sm:px-5">
          <StatGrid>
            <Stat label={t("routes.trips")} value={formatNumber(all.length, locale)} />
            <Stat label={t("routes.distance")} value={formatDistance(totalDistance, locale)} />
            <Stat label={t("routes.corridors")} value={formatNumber(corridors.length, locale)} />
            <Stat
              label={
                <span className="inline-flex items-center gap-1">
                  {t("routes.linked")}
                  <Tooltip content={t("routes.linkedHelp", { name })}>
                    <button type="button" className="rounded-full" aria-label={t("routes.linkedHelp", { name })}>
                      <Info className="size-3" aria-hidden="true" />
                    </button>
                  </Tooltip>
                </span>
              }
              value={`${Math.round((linked / Math.max(1, all.length)) * 100)}%`}
            />
          </StatGrid>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-4">
          <Panel className="relative h-[300px] overflow-hidden sm:h-[360px]">
            <NoraMap
              className="absolute inset-0"
              ariaLabel={t("routes.mapLabel", { name })}
              lines={tripPath.length > 1 ? [{ id: "trip", personId: subjectId, coords: tripPath }] : []}
              frameKey={trip && tripPath.length > 1 ? `trip-${trip.id}` : undefined}
              fitTo={tripPath}
            />
            {trip ? (
              <div className="pointer-events-none absolute inset-x-3 bottom-3 rounded-control border border-hairline bg-surface/95 px-3 py-2 shadow-float backdrop-blur">
                <p className="text-meta font-semibold text-ink">
                  {formatWeekdayDate(trip.startedTs, locale)} · {formatTime(trip.startedTs, locale)}–
                  {formatTime(trip.endedTs, locale)}
                </p>
                <p className="font-mono text-label text-ink-muted tnum">
                  {formatDistance(trip.distanceM, locale)} · {formatDuration(trip.endedTs - trip.startedTs, locale)} ·{" "}
                  {formatNumber(trip.avgSpeedKmh, locale)} km/h
                  {history.isFetching && tripPath.length < 2 ? ` · ${t("routes.loadingPath")}` : ""}
                  {!history.isFetching && tripPath.length < 2 ? ` · ${t("routes.noPath")}` : ""}
                </p>
              </div>
            ) : null}
          </Panel>

          {corridors.length ? (
            <Panel>
              <PanelHeader title={t("routes.corridorsTitle")} description={t("routes.corridorsHelp")} />
              <ul className="divide-y divide-hairline border-t border-hairline">
                {corridors.map((c) => {
                  const n = corridorName(c);
                  const active = corridorId === c.id;
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        aria-pressed={active}
                        onClick={() => {
                          setCorridorId(active ? null : c.id);
                          setTripId(null);
                        }}
                        className={cn(
                          "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-100 hover:bg-hoodie/60 sm:px-5",
                          active && "bg-hoodie/60",
                        )}
                      >
                        <span className="grid min-w-0 flex-1 gap-0.5">
                          <span className="flex min-w-0 items-center gap-1.5 text-[0.9375rem] font-semibold text-ink">
                            {n ? (
                              <>
                                <span className="truncate">{n.from}</span>
                                <ArrowRight className="size-3.5 shrink-0 text-ink-muted" aria-hidden="true" />
                                <span className="truncate">{n.to}</span>
                              </>
                            ) : (
                              t("routes.corridorN", { n: c.id })
                            )}
                          </span>
                          <span className="text-meta text-ink-muted">
                            {t("routes.corridorMeta", {
                              count: c.sampleCount,
                              last: now ? formatRelative(c.lastSeenTs, locale, now) : "",
                            })}
                          </span>
                        </span>
                        {active ? <Badge tone="denim">{t("routes.filtered")}</Badge> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </Panel>
          ) : null}
        </div>

        <Panel className="self-start">
          <PanelHeader
            title={t("routes.tripList")}
            description={corridorId !== null ? t("routes.showingCorridor") : undefined}
          />
          <div className="scroll-thin max-h-[640px] overflow-y-auto border-t border-hairline">
            {byDay.map((g) => (
              <section key={g.day}>
                <h3 className="sticky top-0 z-10 bg-surface/95 px-4 py-2 text-label font-medium text-ink-muted backdrop-blur sm:px-5">
                  {formatWeekdayDate(g.ts, locale)}
                </h3>
                <ul>
                  {g.trips.map((tr) => {
                    const active = trip?.id === tr.id;
                    return (
                      <li key={tr.id}>
                        <button
                          type="button"
                          aria-pressed={active}
                          onClick={() => setTripId(tr.id)}
                          className={cn(
                            "relative flex w-full items-center gap-3 py-2.5 pr-4 pl-7 text-left transition-colors duration-100 hover:bg-hoodie/60 sm:pr-5 sm:pl-8",
                            active && "bg-hoodie/60",
                          )}
                        >
                          {/* The cable: one line down the day, a stop per trip. */}
                          <span className="absolute top-0 bottom-0 left-[15px] w-px bg-hairline sm:left-[19px]" aria-hidden="true" />
                          <span
                            className={cn(
                              "absolute left-[11px] size-[9px] rounded-full border-2 border-surface sm:left-[15px]",
                              active ? "bg-(--person)" : "bg-seam",
                            )}
                            aria-hidden="true"
                          />
                          <span className="grid min-w-0 flex-1 gap-0.5">
                            <span className="font-mono text-meta font-medium text-ink tnum">
                              {formatTime(tr.startedTs, locale)}–{formatTime(tr.endedTs, locale)}
                            </span>
                            <span className="truncate text-label text-ink-muted tnum">
                              {formatDistance(tr.distanceM, locale)} · {formatDuration(tr.endedTs - tr.startedTs, locale)}
                            </span>
                          </span>
                          {tr.corridorId === null ? (
                            <Badge tone="outline">{t("routes.unmatched")}</Badge>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
