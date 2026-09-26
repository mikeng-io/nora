"use client";

import { usePresenceNow } from "@/hooks/use-presence";
import dynamic from "next/dynamic";
import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { ArrowRight, CalendarDays, ChevronRight, Gamepad2, MapPin } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { useViewer } from "@/components/shell/viewer";
import { PageHeader } from "@/components/ui/page-header";
import { PersonAvatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Panel, PanelBody, PanelHeader, Rows } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { TabList, TabPanel, Tabs, useTabParam } from "@/components/ui/tabs";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { PresenceIcon, PresenceMeta, usePresenceWords } from "@/components/presence/presence-text";
import { PlacesPanel } from "@/components/presence/places-panel";
import { RhythmPanel } from "@/components/presence/rhythm-panel";
import { RoutesPanel } from "@/components/presence/routes-panel";
import { MemoryView } from "@/components/nora/memory-view";
import { PlanDate } from "@/components/plans/plan-date";
import { isNotFound } from "@/lib/api/client";
import { fetchPlan } from "@/lib/api/people";
import type { PresenceEntry } from "@/lib/api/presence";
import { qk, useDerivedPlaces, useHistory, usePerson, usePlans, useSavedPlaces } from "@/lib/queries";
import { upcomingPlans } from "@/lib/plans";
import { formatHours, formatTime } from "@/lib/format";
import { hasPosition, isLive, presenceKind as presenceKindOf } from "@/lib/presence";
import { personStyle } from "@/lib/person";
import { useNow } from "@/hooks/use-now";

const NoraMap = dynamic(() => import("@/components/map/nora-map").then((m) => m.NoraMap), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-hoodie" />,
});

const TABS = ["overview", "places", "rhythm", "routes", "memory"] as const;
type Tab = (typeof TABS)[number];

// One person, whole: where they are, where they tend to be, when and how
// they move, which games know them, what you have planned together, and,
// on your own page only, what Nora remembers about you.
export function PersonView({ personId }: { personId: string }) {
  const t = useTranslations("people");
  const tp = useTranslations("patterns");
  const viewer = useViewer();
  const person = usePerson(personId);
  const presence = usePresenceNow();
  const self = personId === viewer.personId;
  const entry = presence.data?.find((p) => p.personId === personId) ?? null;
  const inCircle = entry !== null;
  const [tab, setTab] = useTabParam<Tab>(TABS, "overview");

  if (person.isPending) {
    return (
      <PageFrame>
        <div className="mb-8 flex items-center gap-4">
          <Skeleton className="size-16 rounded-full" />
          <div className="grid gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-56" />
          </div>
        </div>
        <Skeleton className="h-80 rounded-panel" />
      </PageFrame>
    );
  }
  if (person.isError && !person.data) {
    return (
      <PageFrame>
        {isNotFound(person.error) ? (
          <EmptyState
            art="lost"
            title={t("notFoundTitle")}
            action={
              <Link href="/people" className="text-meta font-semibold text-denim hover:underline">
                {t("back")}
              </Link>
            }
          >
            {t("notFound")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => person.refetch()} />
        )}
      </PageFrame>
    );
  }

  const name = person.data.displayName;
  const available: Tab[] = [
    "overview",
    ...(inCircle ? (["places", "rhythm", "routes"] as const) : []),
    ...(self ? (["memory"] as const) : []),
  ];
  const current = available.includes(tab) ? tab : "overview";

  return (
    <PageFrame width="wide">
      <PageHeader
        title={name}
        back={{ href: "/people", label: t("back") }}
        actions={
          inCircle && hasPosition(entry) ? (
            <Link
              href={`/map?person=${personId}`}
              className="inline-flex h-11 items-center gap-2 rounded-control border border-seam px-4 text-[0.9375rem] font-semibold text-ink transition-colors hover:bg-hoodie lg:h-10"
            >
              <MapPin className="size-4" aria-hidden="true" />
              {t("onMap")}
            </Link>
          ) : null
        }
        leading={
          <span style={personStyle(personId)}>
            <PersonAvatar
              id={personId}
              name={name}
              size={60}
              ring={Boolean(entry && isLive(presenceKindOf(entry)))}
            />
          </span>
        }
        eyebrow={self ? <Badge tone="denim">{t("you")}</Badge> : undefined}
        description={entry ? <HeaderPresence entry={entry} /> : t("notInCircle")}
      />

      <Tabs value={current} onValueChange={(v) => setTab(v as Tab)}>
        <TabList
          label={t("sections")}
          className="mb-5 lg:mb-6"
          items={available.map((v) => ({ value: v, label: v === "overview" ? t("overview") : v === "memory" ? t("memory") : tp(`tabs.${v}`) }))}
        />
        <TabPanel value="overview">
          <Overview personId={personId} name={name} entry={entry} self={self} gameProfiles={person.data.gameProfiles} />
        </TabPanel>
        {inCircle ? (
          <>
            <TabPanel value="places">
              <PlacesPanel subjectId={personId} name={name} />
            </TabPanel>
            <TabPanel value="rhythm">
              <RhythmPanel subjectId={personId} name={name} />
            </TabPanel>
            <TabPanel value="routes">
              <RoutesPanel subjectId={personId} name={name} />
            </TabPanel>
          </>
        ) : null}
        {self ? (
          <TabPanel value="memory">
            <MemoryView />
          </TabPanel>
        ) : null}
      </Tabs>
    </PageFrame>
  );
}

function HeaderPresence({ entry }: { entry: PresenceEntry }) {
  const saved = useSavedPlaces();
  const derived = useDerivedPlaces();
  const words = usePresenceWords(entry, saved.data, derived.data?.subjects.find((s) => s.subjectId === entry.personId)?.places);
  return (
    <span className="grid min-w-0">
      <span className="flex min-w-0 items-center gap-1.5 text-meta text-ink-soft">
        <PresenceIcon kind={words.kind} />
        <span className="truncate">{words.primary}</span>
      </span>
      <PresenceMeta words={words} className="text-label" />
    </span>
  );
}

// -------------------------------------------------------------- overview

function Overview({
  personId,
  name,
  entry,
  self,
  gameProfiles,
}: {
  personId: string;
  name: string;
  entry: PresenceEntry | null;
  self: boolean;
  gameProfiles: Array<{ serverId: string; displayName: string; game: string | null }>;
}) {
  const t = useTranslations("people");
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        {entry && hasPosition(entry) ? <TodayMap personId={personId} name={name} entry={entry} /> : null}
        <PlansTogether personId={personId} name={name} self={self} />
      </div>
      <div className="grid gap-4 lg:gap-6">
        {entry ? <PlacesSummary personId={personId} name={name} /> : null}
        <Panel aria-labelledby="games-title">
          <PanelHeader id="games-title" title={t("games")} />
          {gameProfiles.length ? (
            <Rows className="border-t border-hairline">
              {gameProfiles.map((g) => (
                <li key={g.serverId}>
                  <Link
                    href={`/games/${g.serverId}`}
                    className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-hoodie/60 sm:px-5"
                  >
                    <span className="grid size-9 place-items-center rounded-control bg-hoodie text-ink-soft">
                      <Gamepad2 className="size-4" aria-hidden="true" />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-semibold text-ink">{g.displayName}</span>
                    <ChevronRight className="size-4 text-ink-muted" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </Rows>
          ) : (
            <PanelBody>
              <p className="text-body text-ink-soft">{t("noGames", { name })}</p>
            </PanelBody>
          )}
        </Panel>
        {self ? (
          <Link
            href="/nora/memory"
            className="flex items-center gap-3 rounded-panel border border-hairline bg-surface p-4 transition-colors hover:border-seam"
          >
            <span className="min-w-0 flex-1">
              <span className="block text-[0.9375rem] font-semibold text-ink">{t("memoryLink")}</span>
              <span className="block text-meta text-ink-muted">{t("memoryLinkLede")}</span>
            </span>
            <ArrowRight className="size-4 text-ink-muted" aria-hidden="true" />
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function TodayMap({ personId, name, entry }: { personId: string; name: string; entry: PresenceEntry }) {
  const t = useTranslations("people");
  const history = useHistory(personId, 24);
  const fix = entry.current!;
  const coords = useMemo<Array<[number, number]>>(() => {
    const pts = (history.data?.points ?? []).map((p) => [p.lng, p.lat] as [number, number]);
    return pts.length ? [...pts, [fix.lng, fix.lat]] : [];
  }, [history.data, fix.lng, fix.lat]);
  const kind = presenceKindOf(entry);
  return (
    <Panel className="overflow-hidden">
      <PanelHeader title={t("today")} description={t("todayLede", { name })} />
      <div className="relative h-[260px] border-t border-hairline sm:h-[320px]">
        <NoraMap
          className="absolute inset-0"
          ariaLabel={t("todayMap", { name })}
          people={[
            {
              id: personId,
              name,
              lat: fix.lat,
              lng: fix.lng,
              live: isLive(kind),
              faded: !isLive(kind) && kind !== "driving-stale",
              label: name,
            },
          ]}
          selectedId={personId}
          lines={coords.length > 1 ? [{ id: "today", personId, coords }] : []}
          frameKey={coords.length > 1 ? "trail" : "person"}
          fitTo={coords.length > 1 ? coords : [[fix.lng, fix.lat]]}
        />
      </div>
    </Panel>
  );
}

function PlacesSummary({ personId, name }: { personId: string; name: string }) {
  const t = useTranslations("people");
  const tp = useTranslations("patterns");
  const locale = useLocale();
  const derived = useDerivedPlaces();
  const places = [...(derived.data?.subjects.find((s) => s.subjectId === personId)?.places ?? [])]
    .sort((a, b) => b.totalDwellMs - a.totalDwellMs)
    .slice(0, 3);
  if (!derived.data || places.length === 0) return null;
  return (
    <Panel aria-labelledby="where-title">
      <PanelHeader
        id="where-title"
        title={t("usually", { name })}
        action={
          <Link
            href={`/people/${personId}?tab=places`}
            className="inline-flex h-9 items-center gap-1 rounded-control px-2.5 text-meta font-semibold text-denim hover:bg-hoodie"
          >
            {t("allPlaces")}
            <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
        }
      />
      <Rows className="border-t border-hairline">
        {places.map((p, i) => (
          <li key={i} className="flex items-center justify-between gap-3 px-4 py-3 sm:px-5">
            <span className="truncate text-[0.9375rem] text-ink">
              {p.label ?? (p.kind === "home" ? tp("kind.home") : p.kind === "work" ? tp("kind.work") : tp("kind.other", { n: i + 1 }))}
            </span>
            <span className="font-mono text-meta text-ink-muted tnum">{formatHours(p.totalDwellMs, locale)}</span>
          </li>
        ))}
      </Rows>
    </Panel>
  );
}

function PlansTogether({ personId, name, self }: { personId: string; name: string; self: boolean }) {
  const t = useTranslations("people");
  const locale = useLocale();
  const now = useNow();
  const plans = usePlans();
  const upcoming = useMemo(() => upcomingPlans(plans.data ?? [], now || Date.now()).slice(0, 8), [plans.data, now]);
  // The list does not carry who answered; the details do. Read them for the
  // upcoming plans only (a handful), and keep the ones this person is in.
  const details = useQueries({
    queries: self
      ? []
      : upcoming.map((p) => ({
          queryKey: qk.plan(p.id),
          queryFn: ({ signal }: { signal: AbortSignal }) => fetchPlan(p.id, signal),
          staleTime: 60_000,
        })),
  });
  const together = self
    ? upcoming
    : upcoming.filter((p, i) => {
        const d = details[i]?.data;
        return d && (d.created_by === personId || d.rsvps.some((r) => r.person_id === personId && r.response !== "no"));
      });
  const loading = plans.isPending || (!self && details.some((d) => d.isPending));

  return (
    <Panel aria-labelledby="together-title">
      <PanelHeader id="together-title" title={self ? t("yourPlans") : t("together", { name })} />
      {loading ? (
        <PanelBody>
          <Skeleton className="h-14" />
        </PanelBody>
      ) : together.length === 0 ? (
        <PanelBody>
          <p className="flex items-center gap-2 text-body text-ink-soft">
            <CalendarDays className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
            {self ? t("noPlans") : t("noPlansTogether", { name })}
          </p>
        </PanelBody>
      ) : (
        <Rows className="border-t border-hairline">
          {together.map((p) => (
            <li key={p.id}>
              <Link href={`/plans/${p.id}`} className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-hoodie/60 sm:px-5">
                <PlanDate when={p.when} />
                <span className="grid min-w-0 flex-1">
                  <span className="truncate text-[0.9375rem] font-semibold text-ink">{p.title}</span>
                  <span className="truncate text-meta text-ink-muted">
                    {[p.when ? formatTime(Date.parse(p.when), locale) : null, p.venue.name ?? p.venue.text].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <ChevronRight className="size-4 text-ink-muted" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </Rows>
      )}
    </Panel>
  );
}
