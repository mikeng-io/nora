"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Clock, Cpu, HardDrive, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServers, type Server } from "@/lib/games-schema";
import { formatCpuPercent, formatGb, formatUptime } from "@/lib/games-format";
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

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchServers()
        .then((s) => {
          if (!cancelled) {
            setServers(s);
            setError(false);
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
            <ServerCard key={server.id} server={server} locale={locale} />
          ))}
        </div>
      )}
    </div>
  );
}

function ServerCard({ server, locale }: { server: Server; locale: string }) {
  const t = useTranslations("games");
  const current = server.current;
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
            value={current ? formatGb(current.memoryGb) : "—"}
          />
          <StatTile
            icon={Clock}
            label={t("uptime")}
            value={current ? formatUptime(current.uptimeHours * 3600, locale) : "—"}
          />
        </CardContent>
        {current && (
          <p className="border-t border-border px-4 py-2.5 font-mono text-[10px] text-text-muted">
            {t("lastSample")} {formatRelative(current.lastSampleAt, locale)}
          </p>
        )}
      </Card>
    </Link>
  );
}

function formatRelative(iso: string, locale: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  const diffMin = Math.round(diffMs / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  return rtf.format(diffHour, "hour");
}
