"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, Clock, Skull, User } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServerPlayers, type PlayerSummary } from "@/lib/games-schema";
import { formatDurationMs, formatRelativeTime } from "@/lib/games-format";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { StatTile } from "@/components/ui/stat-tile";
import { Pagination } from "@/components/ui/pagination";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

export default function PlayersPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("games");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [onlineOnly, setOnlineOnly] = useState(false);
  const [players, setPlayers] = useState<PlayerSummary[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPlayers(null);
    fetchServerPlayers(id, { page, pageSize: PAGE_SIZE, online: onlineOnly ? true : undefined })
      .then((res) => {
        if (cancelled) return;
        setPlayers(res.data);
        setHasMore(res.pagination.hasMore);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, page, onlineOnly]);

  return (
    <div>
      <Link
        href={`/games/${id}`}
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-[22px] font-medium tracking-tight">{t("playersHeading")}</h1>
        <div className="flex overflow-hidden rounded-md border border-border font-mono text-[11px] uppercase tracking-wide">
          <button
            type="button"
            onClick={() => {
              setOnlineOnly(false);
              setPage(1);
            }}
            className={cn(
              "px-3 py-1.5",
              !onlineOnly ? "bg-accent/15 text-accent" : "text-text-muted hover:bg-bg-elevated",
            )}
          >
            {t("allPlayers")}
          </button>
          <button
            type="button"
            onClick={() => {
              setOnlineOnly(true);
              setPage(1);
            }}
            className={cn(
              "px-3 py-1.5",
              onlineOnly ? "bg-accent/15 text-accent" : "text-text-muted hover:bg-bg-elevated",
            )}
          >
            {t("onlineOnly")}
          </button>
        </div>
      </div>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !players && <p className="text-text-muted">{t("loading")}</p>}
      {!error && players && players.length === 0 && (
        <p className="text-text-muted">{t("noPlayersFound")}</p>
      )}

      {players && players.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {players.map((player) => (
            <Link
              key={player.playerKey}
              href={`/games/${id}/players/${encodeURIComponent(player.playerKey)}`}
            >
              <Card className="transition-colors hover:bg-bg-elevated">
                <CardContent className="flex flex-col gap-3 p-4">
                  <div className="flex items-center justify-between">
                    <span className="truncate text-[14px] font-medium text-text-primary">
                      {player.playerName}
                    </span>
                    <Badge variant={player.online ? "live" : "designed"}>
                      {player.online ? t("online") : t("offline")}
                    </Badge>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <StatTile
                      icon={User}
                      label={t("sessions")}
                      value={String(player.sessionCount)}
                    />
                    <StatTile
                      icon={Clock}
                      label={t("playtime")}
                      value={formatDurationMs(player.totalPlaytimeMs, locale)}
                    />
                    <StatTile
                      icon={Skull}
                      label={t("deaths")}
                      value={String(player.deaths)}
                    />
                  </div>
                  <p className="font-mono text-[10px] text-text-muted">
                    {t("lastSeen")} {formatRelativeTime(player.lastSeenAt, locale)}
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}

      {players && (players.length > 0 || page > 1) && (
        <Pagination
          page={page}
          hasMore={hasMore}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => p + 1)}
          pageLabel={(p) => t("pageLabel", { page: p })}
        />
      )}
    </div>
  );
}
