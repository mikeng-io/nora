"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, Activity, Clock, User, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import {
  fetchServerGraph,
  fetchServerInsights,
  fetchServerStatus,
  fetchServerWeekly,
  fetchServerWorld,
  type Graph,
  type Insights,
  type ServerStatus,
  type WeeklySummary,
  type WorldState,
} from "@/lib/games-schema";
import {
  formatBytes,
  formatCount,
  formatCpuPercent,
  formatDateTime,
  formatDurationMs,
  formatRate,
  formatUptime,
  uptimeSecondsFrom,
} from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { BarList, type BarListItem } from "@/components/ui/bar-list";
import { LineChart, HeatmapGrid, type ChartPoint } from "@/components/ui/mini-chart";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 20_000;
const RANGES = [24, 72, 168] as const;
const RANGE_LABEL_KEY: Record<number, "range24h" | "range72h" | "range7d"> = {
  24: "range24h", 72: "range72h", 168: "range7d",
};
const WEEKDAY_KEYS = ["weekdaySun", "weekdayMon", "weekdayTue", "weekdayWed", "weekdayThu", "weekdayFri", "weekdaySat"] as const;

export default function GameServerPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("games");
  const locale = useLocale();
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [error, setError] = useState(false);
  const [hours, setHours] = useState<(typeof RANGES)[number]>(24);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [weekly, setWeekly] = useState<WeeklySummary | null>(null);
  const [world, setWorld] = useState<WorldState | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);

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
    fetchServerGraph(id, hours)
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch(() => {
        if (!cancelled) setGraph(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id, hours]);

  useEffect(() => {
    let cancelled = false;
    // Fail-soft per section, matching Pulse's own server-detail page — one
    // flaky secondary panel must never blank the whole page.
    Promise.all([
      fetchServerWeekly(id).catch(() => null),
      fetchServerWorld(id).catch(() => null),
      fetchServerInsights(id, 30).catch(() => null),
    ]).then(([w, wo, i]) => {
      if (cancelled) return;
      setWeekly(w);
      setWorld(wo);
      setInsights(i);
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

          <Card className="mb-6">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("network")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 p-4 pt-0">
              <Stat label={t("networkRx")} value={formatRate(status.current.networkRxBps)} />
              <Stat label={t("networkTx")} value={formatRate(status.current.networkTxBps)} />
            </CardContent>
          </Card>

          <Card className="mb-6">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("activePlayersLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <ActivePlayers status={status} noneLabel={t("noActivePlayers")} />
            </CardContent>
          </Card>

          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <NavCard href={`/games/${id}/players`} icon={Users} label={t("playersHeading")} />
            <NavCard href={`/games/${id}/activity`} icon={Activity} label={t("activityHeading")} />
            <NavCard href={`/games/${id}/sessions`} icon={Clock} label={t("sessionsHeading")} />
          </div>

          {weekly && <WeeklyCard weekly={weekly} locale={locale} />}

          {insights && <InsightsSection insights={insights} locale={locale} serverId={id} />}

          {world && <WorldPanels world={world} locale={locale} />}

          <Card className="mb-6">
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <CardTitle>{t("graphLabel")}</CardTitle>
              <div className="flex overflow-hidden rounded-md border border-border font-mono text-[10px] uppercase tracking-wide">
                {RANGES.map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => setHours(r)}
                    className={cn(
                      "px-2.5 py-1",
                      hours === r ? "bg-accent/15 text-accent" : "text-text-muted hover:bg-bg-elevated",
                    )}
                  >
                    {RANGE_LABEL_KEY[r] && t(RANGE_LABEL_KEY[r])}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-5">
              {graph && graph.data.length > 0 ? (
                <>
                  <ChartBlock label={t("chartPlayers")}>
                    <LineChart
                      series={[{
                        label: t("chartPlayers"), color: "var(--accent)",
                        points: graph.data.map((p): ChartPoint => ({ timestamp: p.timestamp, value: p.playerMax })),
                      }]}
                      formatValue={(v) => String(Math.round(v))}
                      formatTime={(ts) => formatDateTime(ts, locale)}
                      yMin={0}
                    />
                  </ChartBlock>
                  <ChartBlock label={t("chartCpu")}>
                    <LineChart
                      series={[{
                        label: t("chartCpu"), color: "var(--accent)",
                        points: graph.data.map((p): ChartPoint => ({ timestamp: p.timestamp, value: p.cpuAvg })),
                      }]}
                      formatValue={formatCpuPercent}
                      formatTime={(ts) => formatDateTime(ts, locale)}
                      yMin={0}
                    />
                  </ChartBlock>
                  <ChartBlock label={t("chartMemory")}>
                    <LineChart
                      series={[{
                        label: t("chartMemory"), color: "var(--accent)",
                        points: graph.data.map((p): ChartPoint => ({ timestamp: p.timestamp, value: p.memoryAvg })),
                      }]}
                      formatValue={formatBytes}
                      formatTime={(ts) => formatDateTime(ts, locale)}
                      yMin={0}
                    />
                  </ChartBlock>
                  <ChartBlock label={t("network")}>
                    <LineChart
                      series={[
                        {
                          label: t("networkRx"), color: "#3d5a80",
                          points: graph.data.map((p): ChartPoint => ({ timestamp: p.timestamp, value: p.networkRxBpsAvg })),
                        },
                        {
                          label: t("networkTx"), color: "#e07a5f",
                          points: graph.data.map((p): ChartPoint => ({ timestamp: p.timestamp, value: p.networkTxBpsAvg })),
                        },
                      ]}
                      formatValue={formatRate}
                      formatTime={(ts) => formatDateTime(ts, locale)}
                      yMin={0}
                    />
                    <div className="mt-1.5 flex gap-4 font-mono text-[9px] text-text-muted">
                      <span className="flex items-center gap-1">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: "#3d5a80" }} />
                        {t("networkRx")}
                      </span>
                      <span className="flex items-center gap-1">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: "#e07a5f" }} />
                        {t("networkTx")}
                      </span>
                    </div>
                  </ChartBlock>
                </>
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

function NavCard({ href, icon: Icon, label }: { href: string; icon: typeof Users; label: string }) {
  return (
    <Link href={href}>
      <Card className="transition-colors hover:bg-bg-elevated">
        <CardContent className="flex items-center gap-2.5 p-4">
          <Icon className="h-4 w-4 text-text-muted" />
          <span className="text-[13px] font-medium text-text-primary">{label}</span>
        </CardContent>
      </Card>
    </Link>
  );
}

function ActivePlayers({ status, noneLabel }: { status: ServerStatus; noneLabel: string }) {
  const names = status.activePlayers;
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

function ChartBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">{label}</p>
      {children}
    </div>
  );
}

function WeeklyCard({ weekly, locale }: { weekly: WeeklySummary; locale: string }) {
  const t = useTranslations("games");
  return (
    <Card className="mb-6">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
          {t("weeklyLabel")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3 p-4 pt-0 sm:grid-cols-4">
        <Stat label={t("weeklyUniquePlayers")} value={String(weekly.uniquePlayers)} />
        <Stat label={t("weeklySessions")} value={String(weekly.sessions)} />
        <Stat label={t("weeklyPlaytime")} value={formatDurationMs(weekly.playtimeMs, locale)} />
        <Stat label={t("weeklyPeakPlayers")} value={String(weekly.peakPlayers)} />
        <Stat label={t("weeklyDeaths")} value={String(weekly.deaths)} />
        <Stat label={t("weeklyAvailability")} value={`${weekly.availabilityPercent.toFixed(0)}%`} />
      </CardContent>
    </Card>
  );
}

function InsightsSection({
  insights, locale, serverId,
}: {
  insights: Insights; locale: string; serverId: string;
}) {
  const t = useTranslations("games");
  const isZhHant = locale.startsWith("zh");

  const heatmapCells = insights.heatmap.map((c) => ({
    weekday: c.weekday, hour: c.hour, value: c.playerHours,
  }));
  const weekdayLabels = WEEKDAY_KEYS.map((k) => t(k));

  return (
    <>
      <Card className="mb-6">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("leaderboardLabel", { days: insights.days })}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-left font-mono text-[10px] uppercase tracking-wide text-text-muted">
                  <th className="pb-2 pr-3 font-normal">{t("players")}</th>
                  <th className="pb-2 pr-3 font-normal">{t("playtime")}</th>
                  <th className="pb-2 pr-3 font-normal">{t("sessions")}</th>
                  <th className="pb-2 pr-3 font-normal">{t("deaths")}</th>
                </tr>
              </thead>
              <tbody>
                {insights.leaderboard.map((entry) => (
                  <tr key={entry.playerKey} className="border-t border-border">
                    <td className="py-1.5 pr-3">
                      <Link
                        href={`/games/${serverId}/players/${encodeURIComponent(entry.playerKey)}`}
                        className="text-text-primary hover:underline"
                      >
                        {entry.playerName}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3 text-text-secondary">
                      {formatDurationMs(entry.playtimeMs, locale)}
                    </td>
                    <td className="py-1.5 pr-3 text-text-secondary">{entry.sessions}</td>
                    <td className="py-1.5 pr-3 text-text-secondary">{entry.deaths}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("deathCausesLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <BarList
              items={insights.deathCauses.map((d): BarListItem => ({
                key: d.cause,
                label: isZhHant ? d.causeZhHant : d.cause,
                value: d.count,
                valueLabel: String(d.count),
              }))}
              emptyLabel={t("noDeathsYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("coplayLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <BarList
              items={insights.coplay.map((c): BarListItem => ({
                key: `${c.aKey}-${c.bKey}`,
                label: `${c.aName} + ${c.bName}`,
                value: c.overlapMs,
                valueLabel: formatDurationMs(c.overlapMs, locale),
              }))}
              emptyLabel={t("noCoplayYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </CardContent>
        </Card>
      </div>

      {insights.killLeaderboard.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("killLeaderboardLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <BarList
              items={insights.killLeaderboard.map((k): BarListItem => ({
                key: k.playerKey,
                label: k.playerName,
                value: k.totalKills,
                valueLabel: String(k.totalKills),
              }))}
              emptyLabel={t("noKillsYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </CardContent>
        </Card>
      )}

      {insights.learnerLeaderboard.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("learnerLeaderboardLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <BarList
              items={insights.learnerLeaderboard.map((l): BarListItem => ({
                key: l.playerKey,
                label: l.playerName,
                value: l.xp,
                valueLabel: formatCount(Math.round(l.xp)),
              }))}
              emptyLabel={t("noLearnersYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </CardContent>
        </Card>
      )}

      {heatmapCells.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("heatmapLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <HeatmapGrid cells={heatmapCells} weekdayLabels={weekdayLabels} />
          </CardContent>
        </Card>
      )}
    </>
  );
}

function WorldPanels({ world, locale }: { world: WorldState; locale: string }) {
  const t = useTranslations("games");
  const isZhHant = locale.startsWith("zh");

  return (
    <>
      <Card className="mb-6">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("worldLabel")}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 p-4 pt-0">
          <div className="grid grid-cols-3 gap-3">
            <Stat label={t("worldDay")} value={String(world.metadata.day)} />
            <Stat label={t("worldItems")} value={formatCount(world.stockpile.totalItems)} />
            <Stat label={t("worldStructures")} value={formatCount(world.stockpile.totalStructures)} />
          </div>
          <div>
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
              {t("stockpileItemsLabel")}
            </p>
            <BarList
              items={world.stockpile.items.map((item): BarListItem => ({
                key: item.id,
                label: isZhHant ? item.zhHant : item.en,
                value: item.count,
                valueLabel: formatCount(item.count),
                unknown: !item.known,
              }))}
              emptyLabel={t("noStockpileYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </div>
        </CardContent>
      </Card>

      {world.timeline.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("explorationLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <ol className="space-y-2">
              {[...world.timeline]
                .sort((a, b) => a.day - b.day)
                .map((sector) => (
                  <li key={sector.id} className="flex items-center gap-2.5 text-[12.5px]">
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                    <span
                      className={sector.known ? "text-text-secondary" : "font-mono text-text-muted"}
                    >
                      {isZhHant ? sector.zhHant : sector.en}
                    </span>
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-text-muted">
                      {t("worldDayN", { day: sector.day })}
                    </span>
                  </li>
                ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </>
  );
}
