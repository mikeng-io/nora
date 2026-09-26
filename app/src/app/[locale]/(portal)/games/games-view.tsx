"use client";

import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronRight } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Ago } from "@/components/ui/ago";
import { PlayerChip, ServerMark, ServerStateBadge } from "@/components/games/game-bits";
import { useServerGraph, useServerStatus, useServers } from "@/lib/queries";
import type { Server } from "@/lib/api/games";
import { serverState } from "@/lib/games";
import { formatDuration, formatNumber } from "@/lib/format";

// The circle's game servers, busiest first: who is on now, and a day of
// players as one quiet line.
export function GamesView() {
  const t = useTranslations("games");
  const servers = useServers();
  const list = useMemo(
    () =>
      [...(servers.data ?? [])].sort(
        (a, b) =>
          (b.current?.playerCount ?? -1) - (a.current?.playerCount ?? -1) ||
          Number(Boolean(b.current?.online)) - Number(Boolean(a.current?.online)) ||
          a.displayName.localeCompare(b.displayName),
      ),
    [servers.data],
  );

  return (
    <PageFrame>
      <PageHeader
        title={t("title")}
        description={t("lede")}
        actions={
          servers.dataUpdatedAt ? (
            <span className="text-meta text-ink-muted">
              {t("checked")} <Ago ts={servers.dataUpdatedAt} />
            </span>
          ) : null
        }
      />
      {servers.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[196px] rounded-panel" />
          ))}
        </div>
      ) : servers.isError && !servers.data ? (
        <UnavailableState what={t("unavailable")} onRetry={() => servers.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState art="quiet">{t("empty")}</EmptyState>
      ) : (
        <ul className="stagger grid gap-4 md:grid-cols-2">
          {list.map((s) => (
            <ServerCard key={s.id} server={s} />
          ))}
        </ul>
      )}
    </PageFrame>
  );
}

function ServerCard({ server: s }: { server: Server }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const state = serverState(s);
  const container = state !== "local";
  const status = useServerStatus(s.id);
  const graph = useServerGraph(s.id, 24, container);
  const players = status.data?.kind === "container" ? (status.data.status.activeProfiles ?? status.data.status.activePlayers.map((n) => ({ playerKey: n, playerName: n }))) : [];
  const local = status.data?.kind === "local-save" ? status.data.status : null;
  const count = s.current?.playerCount ?? 0;

  const spark = graph.data?.data ?? [];
  const peak = Math.max(0, ...spark.map((p) => p.playerMax));
  // The line's scale only: a day with nobody on draws flat, and says so.
  const max = Math.max(1, peak);

  return (
    <li>
      <Link
        href={`/games/${s.id}`}
        className="group grid h-full gap-4 rounded-panel border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-seam sm:p-5"
      >
        <span className="flex items-start gap-3.5">
          <ServerMark color={s.color} size={44} />
          <span className="grid min-w-0 flex-1 gap-1">
            <span className="truncate text-[1.0625rem] font-semibold text-ink">{s.displayName}</span>
            <ServerStateBadge state={state} playing={count > 0} />
          </span>
          <ChevronRight className="mt-1 size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5" aria-hidden="true" />
        </span>

        {container ? (
          <span className="grid gap-2.5">
            <span className="text-meta text-ink-soft tnum">
              {state === "online"
                ? t("playersNow", { count, max: s.maxPlayers ?? 0, hasMax: s.maxPlayers ? "yes" : "no" })
                : t("nobodyOffline")}
            </span>
            {players.length ? (
              <span className="flex flex-wrap gap-1.5">
                {players.slice(0, 6).map((p) => (
                  <PlayerChip key={p.playerKey} name={p.playerName} online />
                ))}
              </span>
            ) : null}
            {spark.length > 1 ? (
              <span className="grid gap-1">
                <svg viewBox={`0 0 ${spark.length - 1} 24`} preserveAspectRatio="none" className="h-8 w-full" aria-hidden="true">
                  <path
                    d={spark.map((p, i) => `${i ? "L" : "M"}${i},${24 - (p.playerMax / max) * 22}`).join("")}
                    fill="none"
                    stroke="var(--denim)"
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                    strokeLinejoin="round"
                  />
                </svg>
                <span className="text-label text-ink-muted">
                  {peak > 0 ? t("sparkLabel", { peak: formatNumber(peak, locale) }) : t("sparkNobody")}
                </span>
              </span>
            ) : null}
          </span>
        ) : local ? (
          <span className="grid gap-1 text-meta text-ink-soft">
            {local.lastPlayedAt ? (
              <span>
                {t("lastPlayed")} <Ago ts={local.lastPlayedAt} />
              </span>
            ) : null}
            {local.totalPlaytimeSeconds !== undefined ? (
              <span className="text-ink-muted">{t("totalPlaytime", { time: formatDuration(local.totalPlaytimeSeconds * 1000, locale) })}</span>
            ) : null}
          </span>
        ) : status.isError ? (
          <span className="text-meta text-ink-muted">{t("statusUnavailable")}</span>
        ) : (
          <Skeleton className="h-10" />
        )}
      </Link>
    </li>
  );
}
