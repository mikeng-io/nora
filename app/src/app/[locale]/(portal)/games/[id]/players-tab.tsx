"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronRight, Skull } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Panel } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Dot } from "@/components/ui/badge";
import { usePlayers } from "@/lib/queries";
import { formatHours, formatNumber, formatRelative } from "@/lib/format";
import { useNow } from "@/hooks/use-now";

// Everyone this server has seen, online first. A row opens the player's
// own page: character, vitals, combat, sessions.
export function PlayersTab({ id }: { id: string }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const now = useNow();
  const [filter, setFilter] = useState<"all" | "online">("all");
  const [page, setPage] = useState(1);
  const players = usePlayers(id, page, filter === "online" ? true : undefined);

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <Segmented
          label={t("playersFilter")}
          value={filter}
          onChange={(f) => {
            setFilter(f);
            setPage(1);
          }}
          options={[
            { value: "all", label: t("allPlayers") },
            { value: "online", label: t("onlineOnly") },
          ]}
        />
        {players.data ? (
          <span className="text-meta text-ink-muted tnum">{t("playersTotal", { count: players.data.pagination.total })}</span>
        ) : null}
      </div>

      {players.isPending ? (
        <Skeleton className="h-80 rounded-panel" />
      ) : players.isError && !players.data ? (
        <UnavailableState onRetry={() => players.refetch()} />
      ) : players.data.data.length === 0 ? (
        <EmptyState compact>{filter === "online" ? t("nobodyOn") : t("noPlayers")}</EmptyState>
      ) : (
        <Panel className={players.isPlaceholderData ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <ul className="divide-y divide-hairline">
            {players.data.data.map((p) => (
              <li key={p.playerKey}>
                <Link
                  href={`/games/${id}/players/${encodeURIComponent(p.playerKey)}`}
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-3 transition-colors hover:bg-hoodie/60 sm:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))_auto] sm:px-5"
                >
                  <span className="flex min-w-0 items-center gap-2.5">
                    <span className="relative grid size-9 shrink-0 place-items-center rounded-full bg-hoodie text-meta font-semibold text-ink-soft">
                      {p.playerName.slice(0, 1).toUpperCase()}
                      {p.online ? <Dot tone="success" live className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full ring-2 ring-surface" /> : null}
                    </span>
                    <span className="grid min-w-0">
                      <span className="truncate text-[0.9375rem] font-semibold text-ink">{p.playerName}</span>
                      <span className="text-label text-ink-muted">
                        {p.online ? t("onlineNow") : now ? t("seen", { when: formatRelative(p.lastSeenAt, locale, now) }) : ""}
                      </span>
                    </span>
                  </span>
                  <span className="hidden font-mono text-meta text-ink tnum sm:block">{formatHours(p.totalPlaytimeMs, locale)}</span>
                  <span className="hidden font-mono text-meta text-ink-soft tnum sm:block">{t("sessionsN", { count: p.sessionCount })}</span>
                  <span className="hidden items-center gap-1 font-mono text-meta text-ink-soft tnum sm:flex">
                    <Skull className="size-3.5 text-ink-muted" aria-hidden="true" />
                    {formatNumber(p.deaths, locale)}
                  </span>
                  <ChevronRight className="size-4 text-ink-muted" aria-hidden="true" />
                  <span className="col-span-2 font-mono text-label text-ink-muted tnum sm:hidden">
                    {formatHours(p.totalPlaytimeMs, locale)} · {t("sessionsN", { count: p.sessionCount })} · {t("deathsN", { count: p.deaths })}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {players.data && (page > 1 || players.data.pagination.hasMore) ? (
        <div className="flex items-center justify-between">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            {t("prev")}
          </Button>
          <span className="text-meta text-ink-muted tnum">{t("pageN", { page })}</span>
          <Button variant="outline" size="sm" disabled={!players.data.pagination.hasMore} onClick={() => setPage((p) => p + 1)}>
            {t("next")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
