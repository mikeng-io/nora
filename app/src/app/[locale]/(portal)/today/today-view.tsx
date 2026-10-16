"use client";

import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ArrowRight, BellRing, ChevronRight, Gamepad2, MapPin } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { useViewer } from "@/components/shell/viewer";
import { useNoraState } from "@/components/shell/nora-presence";
import { NoraAvatar, PersonAvatar } from "@/components/ui/avatar";
import { Badge, Dot } from "@/components/ui/badge";
import { Panel, PanelHeader, Rows } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { RefreshNote } from "@/components/ui/refresh-note";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { PresenceIcon, PresenceMeta, usePresenceWords } from "@/components/presence/presence-text";
import { PlanDate } from "@/components/plans/plan-date";
import {
  useDerivedPlaces,
  useMemory,
  usePlans,
  useRoster,
  useSavedPlaces,
  useServers,
  useStatus,
} from "@/lib/queries";
import type { PresenceEntry } from "@/lib/api/presence";
import { formatDuration, formatLongDate, formatRelative, formatTime, partOfDay } from "@/lib/format";
import { isLive, presenceKind, sortPresence } from "@/lib/presence";
import { upcomingPlans } from "@/lib/plans";
import { episodeOfTheDay } from "@/lib/memory";
import { personStyle } from "@/lib/person";
import { cn } from "@/lib/cn";
import { usePresenceNow } from "@/hooks/use-presence";
import { useNow } from "@/hooks/use-now";

// Today: the portal's front page, and the one new thing the first portal
// never had. A single glance at the circle: who is where, who is playing,
// what is coming, and whether Nora is listening. Every panel links to the
// page that holds the whole of it.
export function TodayView() {
  const t = useTranslations("today");
  const viewer = useViewer();
  const locale = useLocale();
  const now = useNow();
  const first = viewer.displayName.split(/\s+/)[0];
  const greeting = t(`greeting.${partOfDay(now || Date.now())}`, { name: first });
  const ref = useRegisterTitle(t("title"));

  return (
    <PageFrame width="wide">
      <header className="mb-7 grid gap-1.5 lg:mb-10">
        <p className="text-meta font-medium text-ink-muted" suppressHydrationWarning>
          {now ? formatLongDate(now, locale) : " "}
        </p>
        <h1
          ref={ref}
          className="text-[1.875rem] leading-[1.1] font-[650] tracking-[-0.025em] text-ink sm:text-[2.25rem] lg:text-[2.625rem]"
          suppressHydrationWarning
        >
          {now ? greeting : t("title")}
        </h1>
      </header>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] lg:gap-6">
        <div className="stagger grid gap-5 lg:gap-6">
          <NoraStrip />
          <AroundNow />
          <ComingUp />
        </div>
        <div className="stagger grid gap-5 lg:gap-6">
          <PlayingNow />
          <FromHerNotes />
          <Circle />
        </div>
      </div>
    </PageFrame>
  );
}

// ------------------------------------------------------------------ Nora

function NoraStrip() {
  const t = useTranslations("today.nora");
  const locale = useLocale();
  const state = useNoraState();
  const status = useStatus();
  const now = useNow();
  const r = status.data?.reminders;
  const next = r?.next_due_at ? Date.parse(r.next_due_at) : null;

  return (
    <Link
      href="/nora"
      className="group flex items-center gap-4 rounded-panel border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-seam sm:p-5"
    >
      <NoraAvatar size={48} active={state === "listening"} dot={state === "listening"} />
      <div className="min-w-0 flex-1">
        <p className="text-[1rem] font-semibold text-ink">{t(`state.${state}`)}</p>
        <p className="mt-0.5 flex flex-wrap gap-x-2 text-meta text-ink-muted tnum">
          {status.data ? (
            <>
              <span>{t("awake", { duration: formatDuration(status.data.uptime_seconds * 1000, locale) })}</span>
              <span aria-hidden="true">·</span>
              <span>
                {r && r.count > 0 && next
                  ? t("reminders", {
                      count: r.count,
                      when: now ? formatRelative(next, locale, now) : formatTime(next, locale),
                    })
                  : t("noReminders")}
              </span>
            </>
          ) : status.isPending ? (
            <Skeleton className="h-3.5 w-48" />
          ) : (
            <span>{t("unreachableDetail")}</span>
          )}
        </p>
      </div>
      <ChevronRight
        className="size-5 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5"
        aria-hidden="true"
      />
    </Link>
  );
}

// ------------------------------------------------------------ around now

function AroundNow() {
  const t = useTranslations("today.around");
  const presence = usePresenceNow();
  const saved = useSavedPlaces();
  const derived = useDerivedPlaces();

  const entries = useMemo(() => sortPresence(presence.data ?? []), [presence.data]);
  const live = entries.filter((e) => isLive(presenceKind(e)));
  const derivedFor = (id: string) => derived.data?.subjects.find((s) => s.subjectId === id)?.places;

  return (
    <Panel aria-labelledby="around-title">
      <PanelHeader
        id="around-title"
        title={t("title")}
        description={
          presence.data && presence.data.length > 0 ? (
            <span className="flex flex-wrap gap-x-2">
              <span>{t("sharing", { live: live.length, total: entries.length })}</span>
              <RefreshNote query={presence} />
            </span>
          ) : undefined
        }
        action={
          <Link
            href="/map"
            className="inline-flex h-9 items-center gap-1.5 rounded-control px-2.5 text-meta font-semibold text-denim transition-colors hover:bg-hoodie"
          >
            <MapPin className="size-4" aria-hidden="true" />
            {t("openMap")}
          </Link>
        }
      />
      {presence.isPending ? (
        <ul className="grid gap-px px-4 pb-4 sm:px-5">
          {[0, 1, 2].map((i) => (
            <li key={i} className="flex items-center gap-3 py-2.5">
              <Skeleton className="size-10 rounded-full" />
              <div className="grid flex-1 gap-2">
                <Skeleton className="h-3.5 w-32" />
                <Skeleton className="h-3 w-48" />
              </div>
            </li>
          ))}
        </ul>
      ) : presence.isError && !presence.data ? (
        <UnavailableState compact onRetry={() => presence.refetch()} />
      ) : entries.length === 0 ? (
        <EmptyState compact>{t("empty")}</EmptyState>
      ) : (
        <Rows className="border-t border-hairline">
          {entries.map((e) => (
            <AroundRow key={e.personId} entry={e} saved={saved.data} derived={derivedFor(e.personId)} />
          ))}
        </Rows>
      )}
    </Panel>
  );
}

function AroundRow({
  entry,
  saved,
  derived,
}: {
  entry: PresenceEntry;
  saved: Parameters<typeof usePresenceWords>[1];
  derived: Parameters<typeof usePresenceWords>[2];
}) {
  const words = usePresenceWords(entry, saved, derived);
  const live = isLive(words.kind);
  const faded = !live && words.kind !== "driving-stale";
  return (
    <li>
      <Link
        href={`/map?person=${encodeURIComponent(entry.personId)}`}
        className="flex items-center gap-3 px-4 py-3 transition-colors duration-100 hover:bg-hoodie/60 sm:px-5"
        style={personStyle(entry.personId)}
      >
        <span className="relative shrink-0">
          <PersonAvatar
            id={entry.personId}
            name={entry.displayName}
            size={40}
            className={cn(faded && "opacity-55 grayscale-[0.4]")}
          />
          {live ? <Dot tone="person" live className="absolute -right-0.5 -bottom-0.5 size-3 ring-2 ring-surface rounded-full" /> : null}
        </span>
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-[0.9375rem] font-semibold text-ink">{entry.displayName}</span>
          </span>
          <span className={cn("flex min-w-0 items-center gap-1.5 text-meta", faded ? "text-ink-muted" : "text-ink-soft")}>
            <PresenceIcon kind={words.kind} />
            <span className="truncate">{words.primary}</span>
          </span>
          <PresenceMeta words={words} className="sm:hidden" />
        </span>
        <PresenceMeta words={words} className="hidden shrink-0 justify-end sm:inline-flex" />
        <ChevronRight className="size-4 shrink-0 text-ink-muted sm:hidden" aria-hidden="true" />
      </Link>
    </li>
  );
}

// -------------------------------------------------------------- coming up

function ComingUp() {
  const t = useTranslations("today.coming");
  const locale = useLocale();
  const plans = usePlans();
  const status = useStatus();
  const now = useNow();
  const upcoming = useMemo(() => upcomingPlans(plans.data ?? [], now || Date.now()).slice(0, 4), [plans.data, now]);
  const reminders = status.data?.reminders;

  return (
    <Panel aria-labelledby="coming-title">
      <PanelHeader
        id="coming-title"
        title={t("title")}
        action={
          <Link
            href="/plans"
            className="inline-flex h-9 items-center gap-1.5 rounded-control px-2.5 text-meta font-semibold text-denim transition-colors hover:bg-hoodie"
          >
            {t("allPlans")}
            <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
        }
      />
      {plans.isPending ? (
        <div className="grid gap-3 px-4 pb-4 sm:px-5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : plans.isError && !plans.data ? (
        <UnavailableState compact onRetry={() => plans.refetch()} />
      ) : upcoming.length === 0 ? (
        <EmptyState compact art="notebook">
          {t("empty")}
        </EmptyState>
      ) : (
        <Rows className="border-t border-hairline">
          {upcoming.map((p) => (
            <li key={p.id}>
              <Link
                href={`/plans/${p.id}`}
                className="flex items-center gap-4 px-4 py-3 transition-colors duration-100 hover:bg-hoodie/60 sm:px-5"
              >
                <PlanDate when={p.when} />
                <span className="grid min-w-0 flex-1 gap-0.5">
                  <span className="truncate text-[0.9375rem] font-semibold text-ink">{p.title}</span>
                  <span className="truncate text-meta text-ink-muted">
                    {[
                      p.when ? formatTime(Date.parse(p.when), locale) : null,
                      p.venue.name ?? p.venue.text,
                    ]
                      .filter(Boolean)
                      .join(" · ") || t("details")}
                  </span>
                </span>
                {p.status === "proposed" ? <Badge>{t("proposed")}</Badge> : p.status === "staged" ? <Badge>{t("staged")}</Badge> : null}
              </Link>
            </li>
          ))}
        </Rows>
      )}
      {reminders && reminders.count > 0 ? (
        <Link
          href="/nora#reminders"
          className="flex items-center gap-3 border-t border-hairline px-4 py-3 text-meta text-ink-soft transition-colors hover:bg-hoodie/60 sm:px-5"
        >
          <BellRing className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
          <span className="flex-1">
            {t("reminders", {
              count: reminders.count,
              when: reminders.next_due_at
                ? formatRelative(Date.parse(reminders.next_due_at), locale, now || Date.now())
                : "",
            })}
          </span>
          <ChevronRight className="size-4 text-ink-muted" aria-hidden="true" />
        </Link>
      ) : null}
    </Panel>
  );
}

// ------------------------------------------------------------ playing now

function PlayingNow() {
  const t = useTranslations("today.playing");
  const servers = useServers();
  const list = servers.data ?? [];
  const busy = list.filter((s) => s.current?.online && s.current.playerCount > 0);
  const quiet = list.filter((s) => !(s.current?.online && s.current.playerCount > 0));

  return (
    <Panel aria-labelledby="playing-title">
      <PanelHeader
        id="playing-title"
        title={t("title")}
        action={
          <Link
            href="/games"
            className="inline-flex h-9 items-center gap-1.5 rounded-control px-2.5 text-meta font-semibold text-denim transition-colors hover:bg-hoodie"
          >
            <Gamepad2 className="size-4" aria-hidden="true" />
            {t("allGames")}
          </Link>
        }
      />
      {servers.isPending ? (
        <div className="grid gap-3 px-4 pb-4 sm:px-5">
          <Skeleton className="h-12 w-full" />
        </div>
      ) : servers.isError && !servers.data ? (
        <UnavailableState compact onRetry={() => servers.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState compact>{t("noServers")}</EmptyState>
      ) : (
        <>
          {busy.length === 0 ? (
            <p className="px-4 pb-3 text-body text-ink-soft sm:px-5">{t("nobody")}</p>
          ) : null}
          <Rows className="border-t border-hairline">
            {[...busy, ...quiet].map((s) => {
              const online = Boolean(s.current?.online);
              const local = s.current === null;
              const players = s.current?.playerCount ?? 0;
              return (
                <li key={s.id}>
                  <Link
                    href={`/games/${s.id}`}
                    className="flex items-center gap-3 px-4 py-3 transition-colors duration-100 hover:bg-hoodie/60 sm:px-5"
                  >
                    <span
                      className="grid size-9 shrink-0 place-items-center rounded-control text-surface"
                      style={{ background: s.color }}
                      aria-hidden="true"
                    >
                      <Gamepad2 className="size-4.5" />
                    </span>
                    <span className="grid min-w-0 flex-1 gap-0.5">
                      <span className="truncate text-[0.9375rem] font-semibold text-ink">{s.displayName}</span>
                      <span className="flex items-center gap-1.5 text-meta text-ink-muted">
                        {local ? (
                          t("localSave")
                        ) : (
                          <>
                            <Dot tone={online ? "success" : "muted"} live={online && players > 0} />
                            {online ? t("players", { count: players }) : t("offline")}
                          </>
                        )}
                      </span>
                    </span>
                    <ChevronRight className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
                  </Link>
                </li>
              );
            })}
          </Rows>
        </>
      )}
    </Panel>
  );
}

// ------------------------------------------------------ from her notes

function FromHerNotes() {
  const t = useTranslations("today.notes");
  const memory = useMemory();
  const now = useNow();
  const entry = memory.data?.[0];
  const episode = entry ? episodeOfTheDay(entry.episodes, now || Date.now()) : null;

  if (memory.isPending) {
    return (
      <Panel>
        <PanelHeader title={t("title")} />
        <div className="px-4 pb-4 sm:px-5">
          <Skeleton className="h-16 w-full" />
        </div>
      </Panel>
    );
  }
  if (!episode) return null;
  return (
    <Link
      href="/nora/memory"
      className="group grid gap-3 rounded-panel border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-seam sm:p-5"
    >
      <span className="flex items-center gap-2 text-meta font-medium text-ink-muted">
        <NoraAvatar size={22} />
        {t("title")}
      </span>
      <blockquote className="rounded-panel bg-blush-wash px-4 py-3 text-[0.9375rem] leading-relaxed text-ink">
        {episode.text}
      </blockquote>
      <span className="flex items-center gap-1.5 text-meta font-semibold text-denim">
        {t("more")}
        <ArrowRight className="size-4 transition-transform duration-160 ease-nora group-hover:translate-x-0.5" aria-hidden="true" />
      </span>
    </Link>
  );
}

// ---------------------------------------------------------------- circle

function Circle() {
  const t = useTranslations("today.circle");
  const roster = useRoster();
  const viewer = useViewer();
  if (roster.isPending) return null;
  if (!roster.data || roster.data.length === 0) return null;
  return (
    <Panel aria-labelledby="circle-title">
      <PanelHeader
        id="circle-title"
        title={t("title")}
        description={t("count", { count: roster.data.length })}
        action={
          <Link
            href="/people"
            className="inline-flex h-9 items-center gap-1.5 rounded-control px-2.5 text-meta font-semibold text-denim transition-colors hover:bg-hoodie"
          >
            {t("everyone")}
            <ArrowRight className="size-4" aria-hidden="true" />
          </Link>
        }
      />
      <ul className="flex flex-wrap gap-x-1 gap-y-3 px-3 pb-4 sm:px-4">
        {roster.data.map((p) => (
          <li key={p.personId}>
            <Link
              href={`/people/${p.personId}`}
              className="grid w-[72px] justify-items-center gap-1.5 rounded-control py-1.5 transition-colors duration-100 hover:bg-hoodie"
            >
              <PersonAvatar id={p.personId} name={p.displayName} size={40} ring={p.personId === viewer.personId} />
              <span className="w-full truncate px-1 text-center text-label font-medium text-ink-soft">
                {p.displayName}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
