"use client";

import dynamic from "next/dynamic";
import { useCallback, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, ChevronRight, Expand, Minus, Plus, Route, UserRound } from "lucide-react";
import { Link } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { useIsDesktop } from "@/hooks/use-media";
import { useRegisterTitle } from "@/components/shell/page-title";
import { PersonAvatar } from "@/components/ui/avatar";
import { Dot } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Segmented } from "@/components/ui/tabs";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Ago } from "@/components/ui/ago";
import { MapSheet, type Snap } from "@/components/map/map-sheet";
import type { MapPadding, NoraMapHandle } from "@/components/map/nora-map";
import { PresenceIcon, PresenceMeta, usePresenceWords } from "@/components/presence/presence-text";
import { useDerivedPlaces, useHistory, usePresence, useSavedPlaces } from "@/lib/queries";
import type { DerivedPlace, PresenceEntry, SavedPlace } from "@/lib/api/presence";
import { hasPosition, haversineM, isLive, presenceKind, sortPresence } from "@/lib/presence";
import { formatDistance, formatTime } from "@/lib/format";
import { personStyle } from "@/lib/person";

const NoraMap = dynamic(() => import("@/components/map/nora-map").then((m) => m.NoraMap), {
  ssr: false,
  loading: () => <div className="absolute inset-0 bg-hoodie" aria-hidden="true" />,
});

const TRAILS = [0, 1, 6, 24, 168] as const;
type Trail = (typeof TRAILS)[number];

function useMapParams() {
  const params = useSearchParams();
  const person = params.get("person");
  const trailRaw = Number(params.get("trail") ?? 24);
  const trail = (TRAILS as readonly number[]).includes(trailRaw) ? (trailRaw as Trail) : 24;
  const set = useCallback((next: { person?: string | null; trail?: Trail }) => {
    const url = new URL(window.location.href);
    if (next.person !== undefined) {
      if (next.person) url.searchParams.set("person", next.person);
      else url.searchParams.delete("person");
    }
    if (next.trail !== undefined) {
      if (next.trail === 24) url.searchParams.delete("trail");
      else url.searchParams.set("trail", String(next.trail));
    }
    window.history.replaceState(null, "", url);
  }, []);
  return { person, trail, set };
}

export function MapView() {
  const t = useTranslations("map");
  const desktop = useIsDesktop();
  const presence = usePresence();
  const saved = useSavedPlaces();
  const derived = useDerivedPlaces();
  const { person: selectedId, trail, set } = useMapParams();
  const [snap, setSnap] = useState<Snap>(selectedId ? "half" : "peek");
  const [covered, setCovered] = useState(156);
  const [showPlaces, setShowPlaces] = useState(true);
  const mapRef = useRef<NoraMapHandle>(null);
  const titleRef = useRegisterTitle(t("title"));

  const entries = useMemo(() => sortPresence(presence.data ?? []), [presence.data]);
  const selected = entries.find((e) => e.personId === selectedId) ?? null;
  const history = useHistory(selected && trail > 0 ? selected.personId : null, trail || 1);
  const derivedFor = useCallback(
    (id: string) => derived.data?.subjects.find((s) => s.subjectId === id)?.places,
    [derived.data],
  );

  const select = useCallback(
    (id: string | null) => {
      set({ person: id });
      if (id) {
        setSnap((s) => (s === "peek" ? "half" : s));
        const e = entries.find((x) => x.personId === id);
        if (e?.current) mapRef.current?.focus(e.current.lng, e.current.lat);
      }
    },
    [entries, set],
  );

  const people = useMemo(
    () =>
      entries.filter(hasPosition).map((e) => {
        const kind = presenceKind(e);
        return {
          id: e.personId,
          name: e.displayName,
          lat: e.current!.lat,
          lng: e.current!.lng,
          live: isLive(kind),
          faded: !isLive(kind) && kind !== "driving-stale",
          label: `${e.displayName}, ${t(`pinState.${kind}`)}`,
        };
      }),
    [entries, t],
  );

  const trailCoords = useMemo<Array<[number, number]>>(() => {
    if (!selected || trail === 0 || !history.data) return [];
    const pts = history.data.points.map((p) => [p.lng, p.lat] as [number, number]);
    if (selected.current && pts.length) pts.push([selected.current.lng, selected.current.lat]);
    return pts;
  }, [history.data, selected, trail]);

  const padding: MapPadding = desktop
    ? { top: 24, bottom: 24, left: 392, right: 72 }
    : { top: 12, bottom: covered, left: 12, right: 12 };

  // Frame once when the people first arrive, and when a trail loads.
  const frameKey = presence.data ? (trailCoords.length > 1 ? `trail-${selectedId}-${trail}` : "people") : undefined;
  const fitTo = trailCoords.length > 1 ? trailCoords : undefined;
  const liveCount = people.filter((p) => p.live).length;

  const list = (
    <PeopleList
      entries={entries}
      saved={saved.data}
      derivedFor={derivedFor}
      onSelect={select}
      pending={presence.isPending}
      failed={presence.isError && !presence.data}
      retry={() => presence.refetch()}
    />
  );
  const detail = selected ? (
    <PersonDetail
      entry={selected}
      saved={saved.data}
      derived={derivedFor(selected.personId)}
      trail={trail}
      onTrail={(h) => set({ trail: h })}
      trailPoints={history.data?.points}
      trailPending={trail > 0 && history.isFetching && !history.data}
      onBack={() => select(null)}
    />
  ) : null;

  const header = (
    <div className="flex items-baseline justify-between gap-3">
      <h1 ref={titleRef} className="text-[1.0625rem] font-semibold text-ink">
        {t("title")}
      </h1>
      <p className="text-meta text-ink-muted tnum">
        {presence.data ? t("hereNow", { count: liveCount }) : " "}
        {presence.dataUpdatedAt ? (
          <>
            {" · "}
            <Ago ts={presence.dataUpdatedAt} style="narrow" />
          </>
        ) : null}
      </p>
    </div>
  );

  return (
    <div className="relative h-[calc(100dvh-56px-60px-env(safe-area-inset-top)-env(safe-area-inset-bottom))] overflow-hidden lg:h-dvh">
      <NoraMap
        ref={mapRef}
        className="absolute inset-0"
        ariaLabel={t("mapLabel")}
        people={people}
        selectedId={selectedId}
        onSelect={select}
        lines={selected && trailCoords.length > 1 ? [{ id: "trail", personId: selected.personId, coords: trailCoords }] : []}
        places={showPlaces ? (saved.data ?? []) : []}
        padding={padding}
        frameKey={frameKey}
        fitTo={fitTo}
        follow={Boolean(selected && isLive(presenceKind(selected)))}
      />

      <MapControls
        onZoomIn={() => mapRef.current?.zoomIn()}
        onZoomOut={() => mapRef.current?.zoomOut()}
        onFit={() => mapRef.current?.fit(trailCoords.length > 1 ? trailCoords : undefined)}
        showPlaces={showPlaces}
        onTogglePlaces={saved.data?.length ? () => setShowPlaces((v) => !v) : undefined}
      />

      {desktop ? (
        <aside
          aria-label={t("panelLabel")}
          className="absolute top-4 bottom-4 left-4 z-20 flex w-[360px] flex-col overflow-hidden rounded-sheet border border-hairline bg-surface/95 shadow-float backdrop-blur-xl"
        >
          <div className="shrink-0 border-b border-hairline px-5 pt-5 pb-4">{header}</div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
            <div key={selected?.personId ?? "list"} className="page-enter">
              {detail ?? list}
            </div>
          </div>
        </aside>
      ) : (
        <MapSheet
          snap={snap}
          onSnap={setSnap}
          label={t("panelLabel")}
          expandLabel={t("expand")}
          collapseLabel={t("collapse")}
          onCovered={setCovered}
          header={header}
        >
          <div key={selected?.personId ?? "list"} className="animate-fade">
            {detail ?? list}
          </div>
        </MapSheet>
      )}
    </div>
  );
}

// -------------------------------------------------------------- controls

function MapControls({
  onZoomIn,
  onZoomOut,
  onFit,
  showPlaces,
  onTogglePlaces,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  showPlaces: boolean;
  onTogglePlaces?: () => void;
}) {
  const t = useTranslations("map");
  const btn =
    "grid size-11 place-items-center text-ink-soft transition-colors duration-100 hover:bg-hoodie hover:text-ink lg:size-10";
  return (
    <div className="absolute top-3 right-3 z-10 grid gap-2 lg:top-4 lg:right-4">
      <div className="grid overflow-hidden rounded-control border border-hairline bg-surface/95 shadow-float backdrop-blur">
        <button type="button" className={btn} onClick={onZoomIn} aria-label={t("zoomIn")}>
          <Plus className="size-[18px]" aria-hidden="true" />
        </button>
        <span className="mx-2 h-px bg-hairline" aria-hidden="true" />
        <button type="button" className={btn} onClick={onZoomOut} aria-label={t("zoomOut")}>
          <Minus className="size-[18px]" aria-hidden="true" />
        </button>
      </div>
      <button
        type="button"
        className={cn(btn, "rounded-control border border-hairline bg-surface/95 shadow-float backdrop-blur")}
        onClick={onFit}
        aria-label={t("fit")}
      >
        <Expand className="size-[17px]" aria-hidden="true" />
      </button>
      {onTogglePlaces ? (
        <button
          type="button"
          className={cn(
            btn,
            "rounded-control border border-hairline bg-surface/95 shadow-float backdrop-blur",
            showPlaces && "text-denim",
          )}
          onClick={onTogglePlaces}
          aria-pressed={showPlaces}
          aria-label={t("savedPlaces")}
        >
          <span className="size-3 rounded-full bg-surface ring-2 ring-current" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- people

function PeopleList({
  entries,
  saved,
  derivedFor,
  onSelect,
  pending,
  failed,
  retry,
}: {
  entries: PresenceEntry[];
  saved?: SavedPlace[];
  derivedFor: (id: string) => DerivedPlace[] | undefined;
  onSelect: (id: string) => void;
  pending: boolean;
  failed: boolean;
  retry: () => void;
}) {
  const t = useTranslations("map");
  if (pending) {
    return (
      <ul className="grid px-2 py-2">
        {[0, 1, 2, 3].map((i) => (
          <li key={i} className="flex items-center gap-3 px-2 py-2.5">
            <Skeleton className="size-10 rounded-full" />
            <span className="grid flex-1 gap-2">
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-3 w-40" />
            </span>
          </li>
        ))}
      </ul>
    );
  }
  if (failed) return <UnavailableState compact onRetry={retry} />;
  if (entries.length === 0) {
    return (
      <EmptyState compact art="groupChat">
        {t("empty")}
      </EmptyState>
    );
  }
  const here = entries.filter((e) => isLive(presenceKind(e)));
  const rest = entries.filter((e) => !isLive(presenceKind(e)));
  return (
    <div className="grid gap-1 py-2">
      {here.length ? <ListGroup title={t("hereNowTitle")} entries={here} {...{ saved, derivedFor, onSelect }} /> : null}
      {rest.length ? <ListGroup title={t("othersTitle")} entries={rest} {...{ saved, derivedFor, onSelect }} /> : null}
    </div>
  );
}

function ListGroup({
  title,
  entries,
  saved,
  derivedFor,
  onSelect,
}: {
  title: string;
  entries: PresenceEntry[];
  saved?: SavedPlace[];
  derivedFor: (id: string) => DerivedPlace[] | undefined;
  onSelect: (id: string) => void;
}) {
  return (
    <section>
      <h2 className="px-4 pt-2 pb-1 text-label font-medium text-ink-muted lg:px-5">{title}</h2>
      <ul className="px-2 lg:px-3">
        {entries.map((e) => (
          <PersonRow key={e.personId} entry={e} saved={saved} derived={derivedFor(e.personId)} onSelect={onSelect} />
        ))}
      </ul>
    </section>
  );
}

function PersonRow({
  entry,
  saved,
  derived,
  onSelect,
}: {
  entry: PresenceEntry;
  saved?: SavedPlace[];
  derived?: DerivedPlace[];
  onSelect: (id: string) => void;
}) {
  const words = usePresenceWords(entry, saved, derived);
  const live = isLive(words.kind);
  const locatable = hasPosition(entry);
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(entry.personId)}
        disabled={!locatable}
        className="flex w-full items-center gap-3 rounded-control px-2 py-2.5 text-left transition-colors duration-100 enabled:hover:bg-hoodie disabled:cursor-default"
        style={personStyle(entry.personId)}
      >
        <span className="relative shrink-0">
          <PersonAvatar
            id={entry.personId}
            name={entry.displayName}
            size={40}
            className={cn(!live && words.kind !== "driving-stale" && "opacity-55 grayscale-[0.4]")}
          />
          {live ? (
            <Dot tone="person" live className="absolute -right-0.5 -bottom-0.5 size-3 rounded-full ring-2 ring-surface" />
          ) : null}
        </span>
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate text-[0.9375rem] font-semibold text-ink">{entry.displayName}</span>
          <span className="flex min-w-0 items-center gap-1.5 text-meta text-ink-soft">
            <PresenceIcon kind={words.kind} />
            <span className="truncate">{words.primary}</span>
          </span>
          <PresenceMeta words={words} />
        </span>
        {locatable ? <ChevronRight className="size-4 shrink-0 text-ink-muted" aria-hidden="true" /> : null}
      </button>
    </li>
  );
}

// ---------------------------------------------------------------- detail

function PersonDetail({
  entry,
  saved,
  derived,
  trail,
  onTrail,
  trailPoints,
  trailPending,
  onBack,
}: {
  entry: PresenceEntry;
  saved?: SavedPlace[];
  derived?: DerivedPlace[];
  trail: Trail;
  onTrail: (h: Trail) => void;
  trailPoints?: Array<{ lat: number; lng: number; ts: number }>;
  trailPending: boolean;
  onBack: () => void;
}) {
  const t = useTranslations("map");
  const locale = useLocale();
  const words = usePresenceWords(entry, saved, derived);
  const fix = entry.current;

  const distance = useMemo(() => {
    if (!trailPoints || trailPoints.length < 2) return 0;
    let d = 0;
    for (let i = 1; i < trailPoints.length; i++) d += haversineM(trailPoints[i - 1], trailPoints[i]);
    return d;
  }, [trailPoints]);

  return (
    <div className="grid gap-4 px-4 pt-2 pb-4 lg:px-5 lg:pt-4" style={personStyle(entry.personId)}>
      <button
        type="button"
        onClick={onBack}
        className="-ml-1.5 inline-flex h-8 w-fit items-center gap-1 rounded-control pr-2 pl-1 text-meta font-medium text-ink-muted transition-colors hover:bg-hoodie hover:text-ink"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        {t("everyone")}
      </button>

      <div className="flex items-center gap-3.5">
        <PersonAvatar id={entry.personId} name={entry.displayName} size={52} ring={isLive(words.kind)} />
        <div className="grid min-w-0 gap-0.5">
          <h2 className="truncate text-[1.25rem] font-[650] tracking-[-0.01em] text-ink">{entry.displayName}</h2>
          <p className="flex min-w-0 items-center gap-1.5 text-body text-ink-soft">
            <PresenceIcon kind={words.kind} className="size-4" />
            <span className="truncate">{words.primary}</span>
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-panel bg-studio p-3.5">
        {words.address ? (
          <div className="col-span-2 grid gap-0.5">
            <dt className="text-label text-ink-muted">{t("address")}</dt>
            <dd className="text-body text-ink">{words.address}</dd>
          </div>
        ) : fix?.address && words.kind !== "live" ? (
          <div className="col-span-2 grid gap-0.5">
            <dt className="text-label text-ink-muted">{t("lastAddress")}</dt>
            <dd className="text-body text-ink">{fix.address}</dd>
          </div>
        ) : null}
        {fix ? (
          <div className="grid gap-0.5">
            <dt className="text-label text-ink-muted">{t("updated")}</dt>
            <dd className="font-mono text-meta text-ink tnum">
              <Ago ts={fix.ts} />
            </dd>
          </div>
        ) : null}
        {words.battery !== null ? (
          <div className="grid gap-0.5">
            <dt className="text-label text-ink-muted">{t("battery")}</dt>
            <dd className={cn("font-mono text-meta tnum", words.lowBattery ? "text-caution" : "text-ink")}>
              {words.battery}%{words.lowBattery ? ` · ${t("low")}` : ""}
            </dd>
          </div>
        ) : null}
        {words.speed ? (
          <div className="grid gap-0.5">
            <dt className="text-label text-ink-muted">{t("speed")}</dt>
            <dd className="font-mono text-meta text-ink tnum">{words.speed}</dd>
          </div>
        ) : null}
      </dl>

      <section className="grid gap-2.5" aria-labelledby="trail-title">
        <div className="flex items-center justify-between gap-2">
          <h3 id="trail-title" className="flex items-center gap-1.5 text-title font-semibold text-ink">
            <Route className="size-4 text-ink-muted" aria-hidden="true" />
            {t("trail")}
          </h3>
          <Segmented
            size="sm"
            label={t("trail")}
            value={trail}
            onChange={onTrail}
            options={TRAILS.map((h) => ({ value: h, label: t(`trailWindow.${h}`) }))}
          />
        </div>
        <p className="text-meta text-ink-muted tnum">
          {trail === 0
            ? t("trailOff")
            : trailPending
              ? t("trailLoading")
              : trailPoints && trailPoints.length > 1
                ? t("trailSummary", {
                    distance: formatDistance(distance, locale),
                    since: formatTime(trailPoints[0].ts, locale),
                  })
                : t("trailEmpty")}
        </p>
      </section>

      <div className="grid gap-2 sm:grid-cols-2">
        <Link
          href={`/people/${entry.personId}`}
          className="flex h-11 items-center justify-center gap-2 rounded-control border border-seam text-[0.9375rem] font-semibold text-ink transition-colors hover:bg-hoodie lg:h-10"
        >
          <UserRound className="size-4" aria-hidden="true" />
          {t("profile")}
        </Link>
        <Link
          href={`/people/${entry.personId}?tab=routes`}
          className="flex h-11 items-center justify-center gap-2 rounded-control text-[0.9375rem] font-semibold text-ink-soft transition-colors hover:bg-hoodie hover:text-ink lg:h-10"
        >
          {t("patterns")}
          <ChevronRight className="size-4" aria-hidden="true" />
        </Link>
      </div>
    </div>
  );
}
