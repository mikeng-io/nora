"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Clock, Cpu, HardDrive, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServers, type Server } from "@/lib/games-schema";
import { formatBytes, formatCpuPercent, formatUptime, uptimeSecondsFrom } from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";

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
        <div className="grid grid-cols-2 gap-3">
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
          <Metric
            icon={Users}
            label={t("players")}
            value={
              current
                ? `${current.playerCount}${server.maxPlayers ? `/${server.maxPlayers}` : ""}`
                : "—"
            }
          />
          <Metric
            icon={Cpu}
            label={t("cpu")}
            value={current ? formatCpuPercent(current.cpuPercent) : "—"}
          />
          <Metric
            icon={HardDrive}
            label={t("memory")}
            value={current ? formatBytes(current.memoryBytes) : "—"}
          />
          <Metric
            icon={Clock}
            label={t("uptime")}
            value={current ? formatUptime(uptimeSecondsFrom(current), locale) : "—"}
          />
        </CardContent>
      </Card>
    </Link>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md bg-bg-elevated p-2.5">
      <div className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="text-[13px] font-medium text-text-primary">{value}</div>
    </div>
  );
}
