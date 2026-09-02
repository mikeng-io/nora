"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { Activity, Gauge, Grid2X2, MapPinned, Route, Waypoints } from "lucide-react";
import {
  fetchPresenceAnalyticsPlaces,
  fetchPresenceHistory,
  fetchPresenceRhythm,
  fetchPresenceRoutes,
  type PresenceAnalyticsPlace,
  type PresenceEntry,
  type PresenceHistoryPoint,
  type PresenceRhythm,
  type PresenceRoutes,
  type PresenceRouteTrip,
} from "@/lib/presence-schema";
import { hueOf } from "@/lib/person-color";
import { Alert } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Leaflet touches `window` at import time — keep route-map rendering out of SSR.
const RouteMap = dynamic(
  () => import("@/components/presence-route-map").then((m) => m.PresenceRouteMap),
  { ssr: false, loading: () => <div className="h-full w-full bg-bg-elevated" /> },
);

export type MapSurface = "live" | "places" | "rhythm" | "routes";

export function SurfaceTabs({ active, onChange }: { active: MapSurface; onChange: (surface: MapSurface) => void }) {
  const t = useTranslations("map");
  const tabs: { id: MapSurface; icon: typeof MapPinned; label: string }[] = [
    { id: "live", icon: MapPinned, label: t("analytics.tabs.live") },
    { id: "places", icon: Waypoints, label: t("analytics.tabs.places") },
    { id: "rhythm", icon: Grid2X2, label: t("analytics.tabs.rhythm") },
    { id: "routes", icon: Route, label: t("analytics.tabs.routes") },
  ];

  return (
    // A plain, non-ARIA-tabs button group — not role="tablist"/"tab": those
    // roles imply the WAI-ARIA tabs keyboard contract (roving tabindex,
    // arrow-key navigation between tabs), which this 4-item switcher doesn't
    // implement. Announcing "tab 1 of 4" for a widget that doesn't behave
    // like one is worse than not claiming the role.
    <div className="mb-6 flex gap-1 overflow-x-auto border-b border-border [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {tabs.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          type="button"
          aria-pressed={active === id}
          onClick={() => onChange(id)}
          className={cn(
            "relative -mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2.5 font-mono text-[10px] uppercase tracking-wider transition-colors",
            "before:absolute before:-inset-2 before:content-['']", // 44px+ hit target without growing the visible chip
            active === id
              ? "border-accent text-accent"
              : "border-transparent text-text-muted hover:border-border-strong hover:text-text-secondary",
          )}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

export function PresenceAnalytics({
  surface,
  entries,
  selectedSubjectId,
  onSubjectSelect,
}: {
  surface: Exclude<MapSurface, "live">;
  entries: PresenceEntry[] | null;
  selectedSubjectId: string | null;
  onSubjectSelect: (subjectId: string) => void;
}) {
  if (surface === "places") {
    return <PlacesSurface entries={entries} />;
  }
  if (surface === "rhythm") {
    return (
      <RhythmSurface
        entries={entries}
        selectedSubjectId={selectedSubjectId}
        onSubjectSelect={onSubjectSelect}
      />
    );
  }
  return (
    <RoutesSurface
      entries={entries}
      selectedSubjectId={selectedSubjectId}
      onSubjectSelect={onSubjectSelect}
    />
  );
}

function SubjectPicker({
  entries,
  selectedSubjectId,
  onSelect,
}: {
  entries: PresenceEntry[] | null;
  selectedSubjectId: string | null;
  onSelect: (subjectId: string) => void;
}) {
  const t = useTranslations("map");
  if (!entries) return null;

  return (
    <div
      className="mb-4 flex items-center gap-1.5 overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      role="group"
      aria-label={t("analytics.subjects")}
    >
      <span className="mr-1 shrink-0 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("analytics.subjects")}
      </span>
      {entries.map((entry) => {
        const active = entry.personId === selectedSubjectId;
        const hue = hueOf(entry.personId);
        return (
          <button
            key={entry.personId}
            type="button"
            onClick={() => onSelect(entry.personId)}
            className={cn(
              "relative flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] transition-colors",
              "before:absolute before:-inset-2 before:content-['']", // 44px+ hit target without growing the visible pill
              active
                ? "border-border-strong bg-bg-elevated text-text-primary"
                : "border-border text-text-secondary hover:bg-bg-elevated",
            )}
          >
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: `hsl(${hue} 65% 50%)` }} />
            {entry.displayName}
          </button>
        );
      })}
    </div>
  );
}

function PlacesSurface({ entries }: { entries: PresenceEntry[] | null }) {
  const t = useTranslations("map");
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchPresenceAnalyticsPlaces>> | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(false);
    fetchPresenceAnalyticsPlaces()
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <AnalyticsError />;
  if (!data || !entries) return <AnalyticsLoading />;

  const bySubject = new Map(data.subjects.map((subject) => [subject.subjectId, subject.places]));
  return (
    <div className="space-y-3">
      {entries.map((entry) => (
        <PlaceSubjectCard
          key={entry.personId}
          displayName={entry.displayName}
          personId={entry.personId}
          places={bySubject.get(entry.personId) ?? []}
        />
      ))}
      {entries.length === 0 && <AnalyticsEmpty label={t("analytics.empty.noSubjects")} />}
    </div>
  );
}

function PlaceSubjectCard({
  displayName,
  personId,
  places,
}: {
  displayName: string;
  personId: string;
  places: PresenceAnalyticsPlace[];
}) {
  const t = useTranslations("map");
  const hue = hueOf(personId);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: `hsl(${hue} 65% 50%)` }} />
          {displayName}
        </div>
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-muted">
          {places.length} {t("analytics.units.places")}
        </span>
      </div>
      {places.length === 0 ? (
        <div className="px-4 py-5 font-mono text-[11px] text-text-muted">{t("analytics.empty.noPlaces")}</div>
      ) : (
        <div className="divide-y divide-border">
          {places.map((place, index) => <PlaceRow key={`${place.kind}-${place.lat}-${index}`} place={place} />)}
        </div>
      )}
    </Card>
  );
}

function PlaceRow({ place }: { place: PresenceAnalyticsPlace }) {
  const t = useTranslations("map");
  return (
    <div className="grid gap-3 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-wider text-accent">{place.kind}</span>
          <span className="truncate text-[13px] font-medium">{place.label ?? t("analytics.unnamed")}</span>
        </div>
        <div className="mt-1 font-mono text-[10px] text-text-muted">
          {place.lat.toFixed(4)}, {place.lng.toFixed(4)} · {Math.round(place.radiusM)}m · {formatDate(place.firstSeenTs)} → {formatDate(place.lastSeenTs)}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3 text-right sm:min-w-[260px]">
        <Metric label={t("analytics.metrics.confidence")} value={`${Math.round(place.confidence * 100)}%`} />
        <Metric label={t("analytics.metrics.stays")} value={formatNumber(place.stayCount)} />
        <Metric label={t("analytics.metrics.dwell")} value={formatDuration(place.totalDwellMs)} />
      </div>
    </div>
  );
}

function RhythmSurface({
  entries,
  selectedSubjectId,
  onSubjectSelect,
}: {
  entries: PresenceEntry[] | null;
  selectedSubjectId: string | null;
  onSubjectSelect: (subjectId: string) => void;
}) {
  const t = useTranslations("map");
  const [data, setData] = useState<PresenceRhythm | null>(null);
  const [error, setError] = useState(false);
  const subjectId = selectedSubjectId ?? entries?.[0]?.personId ?? null;

  useEffect(() => {
    if (!subjectId) return;
    let cancelled = false;
    setData(null);
    setError(false);
    fetchPresenceRhythm(subjectId)
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  if (!entries) return <AnalyticsLoading />;
  if (!subjectId || entries.length === 0) return <AnalyticsEmpty label={t("analytics.empty.noSubjects")} icon={Grid2X2} />;
  return (
    <div>
      <SubjectPicker entries={entries} selectedSubjectId={subjectId} onSelect={onSubjectSelect} />
      {error ? <AnalyticsError /> : !data ? <AnalyticsLoading /> : <RhythmGrid data={data} subjectId={subjectId} />}
    </div>
  );
}

function RhythmGrid({ data, subjectId }: { data: PresenceRhythm; subjectId: string }) {
  const t = useTranslations("map");
  const bins = useMemo(() => new Map(data.bins.map((bin) => [bin.hourOfWeek, bin])), [data.bins]);
  const maxDwell = Math.max(...data.bins.map((bin) => bin.totalDwellMs), 0);
  const hue = hueOf(subjectId);
  const days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  // A `title` tooltip is mouse-hover-only — invisible to keyboard and touch
  // users. This readout mirrors the same text on focus/hover of any cell so
  // sighted keyboard users get the same detail; aria-label already carries
  // it to screen readers regardless of pointer.
  const [activeCell, setActiveCell] = useState<{ day: number; hour: number } | null>(null);
  const cellDetail = (day: number, hour: number) => {
    const bin = bins.get(day * 24 + hour);
    return `${t(`analytics.days.${days[day]}`)} ${String(hour).padStart(2, "0")}:00 · ${bin?.stayCount ?? 0} ${t("analytics.units.stays")} · ${formatDuration(bin?.totalDwellMs ?? 0)}`;
  };

  return (
    <Card className="p-4 sm:p-5">
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-wider text-text-muted">{t("analytics.rhythm.window")}</div>
          <div className="mt-1 text-[22px] font-medium tracking-tight">{data.windowDays}<span className="ml-1 text-[12px] font-normal text-text-muted">{t("analytics.units.days")}</span></div>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-text-muted">
          <span>{t("analytics.rhythm.low")}</span>
          <span className="h-2 w-16 rounded-sm" style={{ background: `linear-gradient(90deg, hsl(${hue} 68% 50% / .1), hsl(${hue} 68% 50% / .9))` }} />
          <span>{t("analytics.rhythm.high")}</span>
        </div>
      </div>
      <div className="grid grid-cols-[28px_repeat(7,minmax(0,1fr))] gap-1">
        <span />
        {days.map((day) => <span key={day} className="truncate text-center font-mono text-[9px] uppercase tracking-wider text-text-muted">{t(`analytics.days.${day}`)}</span>)}
        {Array.from({ length: 24 }, (_, hour) => (
          <Fragment key={`row-${hour}`}>
            <span className="pt-1 font-mono text-[9px] text-text-muted">{String(hour).padStart(2, "0")}</span>
            {days.map((_, day) => {
              const bin = bins.get(day * 24 + hour);
              const intensity = maxDwell > 0 && bin ? bin.totalDwellMs / maxDwell : 0;
              return (
                <button
                  key={`${day}-${hour}`}
                  type="button"
                  className="aspect-square min-h-5 rounded-[3px] border border-border/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                  style={{ backgroundColor: intensity === 0 ? "var(--bg-elevated)" : `hsl(${hue} 68% 50% / ${0.12 + intensity * 0.78})` }}
                  onFocus={() => setActiveCell({ day, hour })}
                  onMouseEnter={() => setActiveCell({ day, hour })}
                  aria-label={cellDetail(day, hour)}
                />
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className="mt-2 min-h-4 font-mono text-[10px] text-text-muted" aria-live="polite">
        {activeCell ? cellDetail(activeCell.day, activeCell.hour) : " "}
      </div>
      {data.bins.length === 0 && <div className="mt-4 font-mono text-[11px] text-text-muted">{t("analytics.empty.noRhythm")}</div>}
    </Card>
  );
}

function RoutesSurface({
  entries,
  selectedSubjectId,
  onSubjectSelect,
}: {
  entries: PresenceEntry[] | null;
  selectedSubjectId: string | null;
  onSubjectSelect: (subjectId: string) => void;
}) {
  const t = useTranslations("map");
  const [data, setData] = useState<PresenceRoutes | null>(null);
  const [paths, setPaths] = useState<Record<number, PresenceHistoryPoint[]>>({});
  const [selectedTripId, setSelectedTripId] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const subjectId = selectedSubjectId ?? entries?.[0]?.personId ?? null;
  const selectedTrip = data?.trips.find((trip) => trip.id === selectedTripId) ?? data?.trips[0] ?? null;
  const selectedPath = selectedTrip ? paths[selectedTrip.id] ?? [] : [];

  useEffect(() => {
    if (!subjectId) return;
    let cancelled = false;
    setData(null);
    setPaths({});
    setSelectedTripId(null);
    setError(false);
    fetchPresenceRoutes(subjectId)
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  useEffect(() => {
    if (!subjectId || !data || data.trips.length === 0) return;
    let cancelled = false;
    setPaths({});
    fetchPresenceHistory(subjectId, historyHoursForTrips(data.trips))
      .then((value) => {
        const nextPaths = Object.fromEntries(
          data.trips.map((trip) => [
            trip.id,
            value.points.filter((point) => point.ts >= trip.startedTs && point.ts <= trip.endedTs),
          ]),
        );
        if (!cancelled) setPaths(nextPaths);
      })
      .catch(() => {
        if (!cancelled) setPaths({});
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId, data]);

  if (!entries) return <AnalyticsLoading />;
  if (!subjectId || entries.length === 0) return <AnalyticsEmpty label={t("analytics.empty.noSubjects")} icon={Route} />;
  return (
    <div>
      <SubjectPicker entries={entries} selectedSubjectId={subjectId} onSelect={onSubjectSelect} />
      {error ? <AnalyticsError /> : !data ? <AnalyticsLoading /> : data.trips.length === 0 ? <AnalyticsEmpty label={t("analytics.empty.noRoutes")} icon={Route} /> : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <MetricCard icon={Activity} label={t("analytics.routes.trips")} value={formatNumber(data.trips.length)} />
            <MetricCard icon={Waypoints} label={t("analytics.routes.corridors")} value={formatNumber(data.corridors.length)} />
            <MetricCard icon={Gauge} label={t("analytics.routes.linked")} value={formatNumber(data.trips.filter((trip) => trip.corridorId !== null).length)} />
          </div>
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(300px,1.15fr)]">
            <Card className="max-h-[510px] overflow-y-auto">
              <div className="sticky top-0 z-10 border-b border-border bg-bg-surface px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                {t("analytics.routes.tripList")}
              </div>
              <div className="divide-y divide-border">
                {data.trips.map((trip) => (
                  <TripRow key={trip.id} trip={trip} selected={trip.id === selectedTrip?.id} onClick={() => setSelectedTripId(trip.id)} />
                ))}
              </div>
            </Card>
            <Card className="overflow-hidden">
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div className="font-mono text-[10px] uppercase tracking-wider text-text-muted">{t("analytics.routes.path")}</div>
                {selectedTrip && <span className="font-mono text-[10px] text-text-muted">#{selectedTrip.id}</span>}
              </div>
              <div className="h-[390px]">
                {selectedTrip && (
                  <RouteMap
                    routes={data.trips.map((trip) => ({ id: trip.id, corridorId: trip.corridorId, points: paths[trip.id] ?? [] }))}
                    selectedTripId={selectedTrip.id}
                  />
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border px-4 py-2.5 font-mono text-[10px] text-text-muted">
                <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-success" />{t("analytics.routes.start")}</span>
                <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-accent" />{t("analytics.routes.end")}</span>
                {data.corridors.map((corridor) => (
                  <span key={corridor.id} className="flex items-center gap-1.5">
                    <i className="h-2 w-2 rounded-full bg-accent" />C{corridor.id} · {corridor.sampleCount} {t("analytics.routes.samples")}
                  </span>
                ))}
                {selectedPath.length === 0 && <span className="ml-auto">{t("analytics.empty.noPath")}</span>}
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

function TripRow({ trip, selected, onClick }: { trip: PresenceRouteTrip; selected: boolean; onClick: () => void }) {
  const t = useTranslations("map");
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn("w-full px-4 py-3 text-left transition-colors hover:bg-bg-elevated", selected && "bg-bg-elevated")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-text-primary">#{trip.id} · {formatDateTime(trip.startedTs)}</span>
        {trip.corridorId !== null ? <span className="rounded-full bg-accent/10 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-accent">{t("analytics.routes.corridor")} {trip.corridorId}</span> : <span className="font-mono text-[9px] uppercase tracking-wider text-text-muted">{t("analytics.routes.unmatched")}</span>}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-[10px] text-text-muted">
        <span>{(trip.distanceM / 1000).toFixed(1)} km</span>
        <span>{trip.avgSpeedKmh.toFixed(1)} km/h</span>
        <span>{trip.fixCount} {t("analytics.units.fixes")}</span>
      </div>
      {trip.corridorSampleCount !== null && <div className="mt-1 font-mono text-[9px] uppercase tracking-wider text-accent">{trip.corridorSampleCount} {t("analytics.routes.samples")}</div>}
    </button>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><div className="font-mono text-[9px] uppercase tracking-wider text-text-muted">{label}</div><div className="mt-1 font-mono text-[12px] text-text-primary">{value}</div></div>;
}

function MetricCard({ icon: Icon, label, value }: { icon: typeof Activity; label: string; value: string }) {
  return <Card className="flex items-center gap-3 p-4"><Icon className="h-4 w-4 text-accent" /><div><div className="font-mono text-[9px] uppercase tracking-wider text-text-muted">{label}</div><div className="mt-1 font-mono text-[17px]">{value}</div></div></Card>;
}

function AnalyticsLoading() {
  const t = useTranslations("map");
  return <div className="flex min-h-32 items-center justify-center font-mono text-[10px] uppercase tracking-wider text-text-muted">{t("analytics.loading")}</div>;
}

function AnalyticsError() {
  const t = useTranslations("map");
  return <Alert variant="destructive">{t("analytics.error")}</Alert>;
}

function AnalyticsEmpty({ label, icon: Icon = Waypoints }: { label: string; icon?: typeof Waypoints }) {
  return (
    <div className="flex min-h-32 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-strong font-mono text-[10px] uppercase tracking-wider text-text-muted">
      <Icon className="h-4 w-4 opacity-60" />
      {label}
    </div>
  );
}

function formatNumber(value: number) {
  return new Intl.NumberFormat().format(value);
}

function formatDuration(ms: number) {
  if (ms < 60 * 60 * 1000) return `${Math.max(1, Math.round(ms / (60 * 1000)))}m`;
  return `${(ms / (60 * 60 * 1000)).toFixed(1)}h`;
}

function formatDate(ts: number) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(ts);
}

function formatDateTime(ts: number) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(ts);
}

function historyHoursForTrips(trips: PresenceRouteTrip[]) {
  const earliestTrip = Math.min(...trips.map((trip) => trip.startedTs));
  const hoursFromNow = Math.ceil((Date.now() - earliestTrip) / (60 * 60 * 1000));
  return Math.min(24 * 30, Math.max(1, hoursFromNow + 1));
}
