"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, User } from "lucide-react";
import { Link } from "@/i18n/routing";
import {
  fetchServerGraph,
  fetchServerStatus,
  type Graph,
  type ServerStatus,
} from "@/lib/games-schema";
import { formatBytes, formatCpuPercent, formatRate, formatUptime, uptimeSecondsFrom } from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";

const POLL_INTERVAL_MS = 20_000;

export default function GameServerPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("games");
  const locale = useLocale();
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchServerStatus(id)
        .then((s) => {
          if (!cancelled) {
            setStatus(s);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    tick();
    const poll = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(poll);
    };
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    fetchServerGraph(id, 24)
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {
        if (!cancelled) setGraph(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div>
      <Link
        href="/games"
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !status && <p className="text-text-muted">{t("loading")}</p>}

      {status && (
        <>
          <div className="mb-6 flex items-center gap-2.5">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: status.server.color }}
            />
            <h1 className="text-[26px] font-medium tracking-tight">{status.server.displayName}</h1>
            <Badge variant={status.current.online ? "live" : "danger"}>
              {status.current.online
                ? t("online")
                : status.current.status === "restarting"
                  ? t("restarting")
                  : t("offline")}
            </Badge>
          </div>

          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat
              label={t("players")}
              value={`${status.current.playerCount}${status.server.maxPlayers ? `/${status.server.maxPlayers}` : ""}`}
            />
            <Stat label={t("cpu")} value={formatCpuPercent(status.current.cpuPercent)} />
            <Stat label={t("memory")} value={formatBytes(status.current.memoryBytes)} />
            <Stat
              label={t("uptime")}
              value={formatUptime(uptimeSecondsFrom(status.current), locale)}
            />
          </div>

          <div className="mb-6 grid grid-cols-2 gap-3">
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  {t("network")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0 text-[13px] text-text-secondary">
                ↓ {formatRate(status.current.networkRxBps)} · ↑ {formatRate(status.current.networkTxBps)}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  {t("activePlayersLabel")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <ActivePlayers status={status} noneLabel={t("noActivePlayers")} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{t("graphLabel")}</CardTitle>
            </CardHeader>
            <CardContent>
              {graph && graph.data.length > 0 ? (
                <PlayerSparkline graph={graph} />
              ) : (
                <p className="font-mono text-[12.5px] text-text-muted">{t("graphUnavailable")}</p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="mb-1 font-mono text-[10px] uppercase tracking-wider text-text-muted">{label}</p>
        <p className="text-[15px] font-medium text-text-primary">{value}</p>
      </CardContent>
    </Card>
  );
}

function ActivePlayers({ status, noneLabel }: { status: ServerStatus; noneLabel: string }) {
  const names = status.activeProfiles?.map((p) => p.playerName) ?? status.activePlayers;
  if (names.length === 0) {
    return <p className="font-mono text-[12.5px] text-text-muted">{noneLabel}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {names.map((name, i) => (
        <li key={i} className="flex items-center gap-1.5 text-[13px] text-text-secondary">
          <User className="h-3 w-3 text-text-muted" />
          {name}
        </li>
      ))}
    </ul>
  );
}

// A dependency-free line, so the module doesn't need to pull `recharts` in
// just to show one series — this is one line, not a full charting surface.
function PlayerSparkline({ graph }: { graph: Graph }) {
  const width = 600;
  const height = 80;
  const points = graph.data.map((p) => p.playerMax);
  const max = Math.max(1, ...points);
  const stepX = points.length > 1 ? width / (points.length - 1) : 0;
  const path = points
    .map((v, i) => {
      const x = i * stepX;
      const y = height - (v / max) * height;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-20 w-full"
      role="img"
      aria-label="players online over the last 24 hours"
    >
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  );
}
