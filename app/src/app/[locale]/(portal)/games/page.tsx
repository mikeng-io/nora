"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Clock, Cpu, HardDrive, Upload, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServers, fetchServerStatus, type LocalSaveStatus, type Server } from "@/lib/games-schema";
import {
  formatBytes, formatCpuPercent, formatDurationMs, formatRelativeTime, formatUptime, uptimeSecondsFrom,
} from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { StatTile } from "@/components/ui/stat-tile";

const POLL_INTERVAL_MS = 20_000;

export default function GamesPage() {
  const t = useTranslations("games");
  const locale = useLocale();
  const [servers, setServers] = useState<Server[] | null>(null);
  const [error, setError] = useState(false);
  // `/v1/servers`' `current` is container metrics only — null for a
  // local-save game (Big Walk has no container to poll), not "offline".
  // Its own `/status` answers with the last upload's facts instead.
  const [localStatuses, setLocalStatuses] = useState<Record<string, LocalSaveStatus>>({});

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchServers()
        .then((s) => {
          if (cancelled) return;
          setServers(s);
          setError(false);
          for (const server of s) {
            if (server.current !== null) continue;
            fetchServerStatus(server.id)
              .then((status) => {
                if (cancelled || status.kind !== "local-save") return;
                setLocalStatuses((prev) => ({ ...prev, [server.id]: status.status }));
              })
              .catch(() => {
                // Fail-soft per card — the list itself must not blank.
              });
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
      {!error && !servers && <p className="text-text-muted">{t("loading")}</p>}
      {!error && servers && servers.length === 0 && (
        <p className="text-text-muted">{t("empty")}</p>
      )}

      {servers && servers.length > 0 && (
        // A rigid multi-column grid with exactly one server (Pulse's real,
        // current state — one game adapter configured) leaves a lone
        // narrow card floating beside a lot of dead space. Only grid up
        // once there's more than one to actually arrange.
        <div className={servers.length > 1 ? "grid grid-cols-1 gap-3 sm:grid-cols-2" : "grid grid-cols-1 gap-3"}>
          {servers.map((server) => (
            <ServerCard
              key={server.id} server={server} locale={locale}
              localStatus={localStatuses[server.id]}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ServerCard(
  { server, locale, localStatus }: { server: Server; locale: string; localStatus?: LocalSaveStatus },
) {
  const t = useTranslations("games");
  const current = server.current;
  const isLocalSave = current === null;

  if (isLocalSave) {
    return (
      <Link href={`/games/${server.id}`} className="block">
        <Card className="transition-colors hover:bg-bg-elevated">
          <CardHeader className="flex-row items-center justify-between space-y-0 p-4 pb-2">
            <div className="flex items-center gap-2">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: server.color }}
              />
              <CardTitle>{server.displayName}</CardTitle>
            </div>
            <Badge variant="designed">{t("localSaveBadge")}</Badge>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2 p-4 pt-0">
            <StatTile
              icon={Upload}
              label={t("lastUploaded")}
              value={localStatus ? formatRelativeTime(localStatus.lastUploadedAt, locale) : "—"}
            />
            <StatTile
              icon={Clock}
              label={t("playtime")}
              value={
                localStatus?.totalPlaytimeSeconds !== undefined
                  ? formatDurationMs(localStatus.totalPlaytimeSeconds * 1000, locale)
                  : "—"
              }
            />
          </CardContent>
        </Card>
      </Link>
    );
  }

  const online = current?.online ?? false;

  return (
    <Link href={`/games/${server.id}`} className="block">
      <Card className="transition-colors hover:bg-bg-elevated">
        <CardHeader className="flex-row items-center justify-between space-y-0 p-4 pb-2">
          <div className="flex items-center gap-2">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: server.color }}
            />
            <CardTitle>{server.displayName}</CardTitle>
          </div>
          <Badge variant={online ? "live" : "danger"}>
            {online
              ? t("online")
              : current?.status === "restarting"
                ? t("restarting")
                : t("offline")}
          </Badge>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 p-4 pt-0">
          <StatTile
            icon={Users}
            label={t("players")}
            value={
              current
                ? `${current.playerCount}${server.maxPlayers ? `/${server.maxPlayers}` : ""}`
                : "—"
            }
          />
          <StatTile
            icon={Cpu}
            label={t("cpu")}
            value={current ? formatCpuPercent(current.cpuPercent) : "—"}
          />
          <StatTile
            icon={HardDrive}
            label={t("memory")}
            value={current ? formatBytes(current.memoryBytes) : "—"}
          />
          <StatTile
            icon={Clock}
            label={t("uptime")}
            value={current ? formatUptime(uptimeSecondsFrom(current), locale) : "—"}
          />
        </CardContent>
        {current && (
          <p className="border-t border-border px-4 py-2.5 font-mono text-[10px] text-text-muted">
            {t("lastSample")} {formatRelative(current.timestamp, locale)}
          </p>
        )}
      </Card>
    </Link>
  );
}

function formatRelative(timestampMs: number, locale: string): string {
  const diffMs = timestampMs - Date.now();
  const diffMin = Math.round(diffMs / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  return rtf.format(diffHour, "hour");
}
