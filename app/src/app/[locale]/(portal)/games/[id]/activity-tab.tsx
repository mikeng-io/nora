"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { LogIn, LogOut, Skull, Sparkle } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Panel } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Dot } from "@/components/ui/badge";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import type { ActivityEvent, Session } from "@/lib/api/games";
import { useActivity, useSessions } from "@/lib/queries";
import { dayIndex, formatDuration, formatTime, formatWeekdayDate } from "@/lib/format";
import { localized } from "@/lib/games";
import { cn } from "@/lib/cn";

// What happened on the server, newest first, along one line per day:
// joins, leaves, deaths, and the world's own events. Sessions are the same
// history counted in stays rather than moments.
export function ActivityTab({ id, local }: { id: string; local: boolean }) {
  const t = useTranslations("games");
  const [view, setView] = useState<"events" | "sessions">("events");
  return (
    <div className="grid gap-4">
      {!local ? (
        <Segmented
          label={t("activityView")}
          value={view}
          onChange={setView}
          options={[
            { value: "events", label: t("events") },
            { value: "sessions", label: t("sessions") },
          ]}
          className="w-fit"
        />
      ) : null}
      {view === "events" ? <Events id={id} /> : <Sessions id={id} />}
    </div>
  );
}

function groupByDay<T>(items: T[], ts: (x: T) => number) {
  const out: Array<{ day: number; ts: number; items: T[] }> = [];
  for (const it of items) {
    const d = dayIndex(ts(it));
    const g = out[out.length - 1];
    if (g && g.day === d) g.items.push(it);
    else out.push({ day: d, ts: ts(it), items: [it] });
  }
  return out;
}

function Pager({ page, hasMore, setPage }: { page: number; hasMore: boolean; setPage: (p: number) => void }) {
  const t = useTranslations("games");
  if (page <= 1 && !hasMore) return null;
  return (
    <div className="flex items-center justify-between">
      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
        {t("newer")}
      </Button>
      <span className="text-meta text-ink-muted tnum">{t("pageN", { page })}</span>
      <Button variant="outline" size="sm" disabled={!hasMore} onClick={() => setPage(page + 1)}>
        {t("older")}
      </Button>
    </div>
  );
}

const ICON = {
  "player.joined": LogIn,
  "player.left": LogOut,
  "player.died": Skull,
  "world.event": Sparkle,
} as const;

function Events({ id }: { id: string }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const activity = useActivity(id, page);

  if (activity.isPending) return <Skeleton className="h-80 rounded-panel" />;
  if (activity.isError && !activity.data) return <UnavailableState onRetry={() => activity.refetch()} />;
  if (activity.data.data.length === 0) return <EmptyState compact>{t("noActivity")}</EmptyState>;

  const line = (e: ActivityEvent) => {
    const name = e.playerName ?? "";
    if (e.type === "player.joined") return t("event.joined", { name });
    if (e.type === "player.left") return t("event.left", { name });
    if (e.type === "player.died") {
      const cause = localized(e.deathCause, e.deathCauseZhHant, locale);
      return cause ? t("event.diedOf", { name, cause }) : t("event.died", { name });
    }
    return localized(e.message, e.messageZhHant, locale);
  };

  return (
    <>
      <Panel className={cn("transition-opacity", activity.isPlaceholderData && "opacity-60")}>
        {groupByDay(activity.data.data, (e) => e.occurredAt).map((g) => (
          <section key={g.day}>
            <h3 className="sticky top-14 z-10 border-b border-hairline bg-surface/95 px-4 py-2 text-label font-medium text-ink-muted backdrop-blur first:rounded-t-panel sm:px-5 lg:top-0">
              {formatWeekdayDate(g.ts, locale)}
            </h3>
            <ol className="relative px-4 py-2 sm:px-5">
              <span className="absolute top-4 bottom-4 left-[31px] w-px bg-hairline sm:left-[35px]" aria-hidden="true" />
              {g.items.map((e) => {
                const Icon = ICON[e.type];
                return (
                  <li key={e.id} className="relative flex items-start gap-3 py-2">
                    <span
                      className={cn(
                        "relative z-10 grid size-8 shrink-0 place-items-center rounded-full border border-hairline bg-surface",
                        e.type === "player.died" ? "text-danger" : "text-ink-muted",
                      )}
                    >
                      <Icon className="size-3.5" aria-hidden="true" />
                    </span>
                    <span className="grid min-w-0 flex-1 gap-0.5 pt-1">
                      <span className="text-body text-ink">{line(e)}</span>
                    </span>
                    <time className="shrink-0 pt-1.5 font-mono text-label text-ink-muted tnum">{formatTime(e.occurredAt, locale)}</time>
                  </li>
                );
              })}
            </ol>
          </section>
        ))}
      </Panel>
      <Pager page={page} hasMore={activity.data.pagination.hasMore} setPage={setPage} />
    </>
  );
}

function Sessions({ id }: { id: string }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const sessions = useSessions(id, page);

  if (sessions.isPending) return <Skeleton className="h-80 rounded-panel" />;
  if (sessions.isError && !sessions.data) return <UnavailableState onRetry={() => sessions.refetch()} />;
  if (sessions.data.data.length === 0) return <EmptyState compact>{t("noSessions")}</EmptyState>;

  return (
    <>
      <Panel className={cn("transition-opacity", sessions.isPlaceholderData && "opacity-60")}>
        {groupByDay(sessions.data.data, (s: Session) => s.joinedAt).map((g) => (
          <section key={g.day}>
            <h3 className="border-b border-hairline px-4 py-2 text-label font-medium text-ink-muted sm:px-5">
              {formatWeekdayDate(g.ts, locale)}
            </h3>
            <ul className="divide-y divide-hairline">
              {g.items.map((s) => {
                const open = s.leftAt === null;
                return (
                  <li key={s.id}>
                    <Link
                      href={`/games/${id}/players/${encodeURIComponent(s.playerKey)}`}
                      className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-hoodie/60 sm:px-5"
                    >
                      <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{s.playerName}</span>
                      <span className="font-mono text-meta text-ink-soft tnum">
                        {formatTime(s.joinedAt, locale)}–{s.leftAt ? formatTime(s.leftAt, locale) : ""}
                      </span>
                      <span className="w-20 text-right font-mono text-meta text-ink tnum">
                        {open ? (
                          <span className="inline-flex items-center gap-1.5 text-success">
                            <Dot tone="success" live />
                            {t("onlineNow")}
                          </span>
                        ) : (
                          formatDuration(s.durationMs ?? 0, locale)
                        )}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </Panel>
      <Pager page={page} hasMore={sessions.data.pagination.hasMore} setPage={setPage} />
    </>
  );
}
