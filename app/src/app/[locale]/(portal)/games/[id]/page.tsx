"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, Activity, Clock, User, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import {
  fetchServerGraph,
  fetchServerInsights,
  fetchServerProgress,
  fetchServerSaveHistory,
  fetchServerStatus,
  fetchServerWeekly,
  fetchServerWorld,
  type AnyServerStatus,
  type AnyWorldState,
  type BigWalkWorld,
  type GameProgress,
  type Graph,
  type Insights,
  type SaveHistory,
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
} from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { BarList, type BarListItem } from "@/components/ui/bar-list";
import { LineChart, HeatmapGrid, type ChartPoint } from "@/components/ui/mini-chart";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { Skeleton } from "@/components/ui/skeleton";
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
  const [status, setStatus] = useState<AnyServerStatus | null>(null);
  const [error, setError] = useState(false);
  const [hours, setHours] = useState<(typeof RANGES)[number]>(24);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [weekly, setWeekly] = useState<WeeklySummary | null>(null);
  const [world, setWorld] = useState<AnyWorldState | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [progress, setProgress] = useState<GameProgress | null>(null);
  const [saveHistory, setSaveHistory] = useState<SaveHistory | null>(null);

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

  const isLocalSave = status?.kind === "local-save";
  // Stable primitives to depend on instead of `status` itself — a fresh
  // status object is allocated every 20s poll tick (`fetchServerStatus`
  // never returns the same reference twice), so depending on `status`
  // directly re-ran these effects on every tick forever, hammering Pulse
  // with full insights/weekly aggregation queries every 20 seconds instead
  // of once per page view.
  const statusKind = status?.kind ?? null;
  const lastUploadedAt = status?.kind === "local-save" ? status.status.lastUploadedAt : undefined;

  // Container-backed history (CPU/memory/network/players over time) — no
  // container, no route, for a local-save game.
  useEffect(() => {
    if (!statusKind || statusKind === "local-save") return;
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
  }, [id, hours, statusKind]);

  // Local-save history (playtime/completion counts per upload) — the
  // save-upload equivalent, fetched only once we know which kind this is,
  // and again whenever a new save actually lands.
  useEffect(() => {
    if (statusKind !== "local-save") return;
    let cancelled = false;
    Promise.all([
      fetchServerProgress(id).catch(() => null),
      fetchServerSaveHistory(id, hours).catch(() => null),
    ]).then(([p, sh]) => {
      if (cancelled) return;
      setProgress(p);
      setSaveHistory(sh);
    });
    return () => {
      cancelled = true;
    };
  }, [id, hours, statusKind, lastUploadedAt]);

  // World detail (checklist/crew/notes) — refetched only when a new save
  // upload actually lands, not on every status poll tick.
  useEffect(() => {
    let cancelled = false;
    fetchServerWorld(id)
      .catch(() => null)
      .then((wo) => {
        if (!cancelled) setWorld(wo);
      });
    return () => {
      cancelled = true;
    };
  }, [id, lastUploadedAt]);

  // Weekly summary and insights (leaderboard, death causes, coplay) are built
  // from container-log-derived sessions and kill events — a local-save game
  // like Big Walk has neither, so these panels have nothing to show and are
  // skipped entirely rather than rendered empty.
  useEffect(() => {
    if (!statusKind || statusKind === "local-save") return;
    let cancelled = false;
    Promise.all([
      fetchServerWeekly(id).catch(() => null),
      fetchServerInsights(id, 30).catch(() => null),
    ]).then(([w, i]) => {
      if (cancelled) return;
      setWeekly(w);
      setInsights(i);
    });
    return () => {
      cancelled = true;
    };
  }, [id, statusKind]);

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
      {!error && !status && <ServerDetailSkeleton />}

      {status?.kind === "container" && (
        <ContainerHeader status={status.status} />
      )}

      {status?.kind === "local-save" && (
        <LocalSaveServerView
          status={status.status} id={id} locale={locale} hours={hours} setHours={setHours}
          progress={progress} saveHistory={saveHistory}
        />
      )}

      {status && (
        <>
          {/* Players and Sessions come from the abiotic-log activity watcher a
              local-save game has no config for — Pulse answers both with a
              well-formed empty page rather than an error, which reads as
              missing data rather than a section that doesn't apply. Activity
              stays: Big Walk's own save-diff events populate it for real. */}
          <div className={cn("mb-6 grid grid-cols-1 gap-3", !isLocalSave && "sm:grid-cols-3")}>
            {!isLocalSave && (
              <NavCard href={`/games/${id}/players`} icon={Users} label={t("playersHeading")} />
            )}
            <NavCard href={`/games/${id}/activity`} icon={Activity} label={t("activityHeading")} />
            {!isLocalSave && (
              <NavCard href={`/games/${id}/sessions`} icon={Clock} label={t("sessionsHeading")} />
            )}
          </div>

          {weekly && <WeeklyCard weekly={weekly} locale={locale} />}

          {insights && <InsightsSection insights={insights} locale={locale} serverId={id} />}

          {world?.kind === "abiotic-factor" && (
            <AbioticWorldPanels world={world.world} locale={locale} />
          )}
          {world?.kind === "big-walk" && (
            <BigWalkWorldPanels world={world.world} locale={locale} />
          )}

          {status?.kind === "container" && (
            <ContainerTelemetryDetails
              status={status.status} locale={locale} hours={hours} setHours={setHours} graph={graph}
            />
          )}
        </>
      )}
    </div>
  );
}

function ServerDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2.5">
        <Skeleton className="h-2.5 w-2.5 rounded-full" />
        <Skeleton className="h-7 w-40" />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-16" />)}
      </div>
      <Skeleton className="h-32" />
    </div>
  );
}

function RangeToggle({
  hours, setHours,
}: {
  hours: (typeof RANGES)[number]; setHours: (h: (typeof RANGES)[number]) => void;
}) {
  const t = useTranslations("games");
  return (
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
          {t(RANGE_LABEL_KEY[r])}
        </button>
      ))}
    </div>
  );
}

function ContainerHeader({
  status,
}: {
  status: Extract<AnyServerStatus, { kind: "container" }>["status"];
}) {
  const t = useTranslations("games");

  return (
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

      <Card className="mb-6">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("activePlayersLabel")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <p className="mb-2 text-[13px] text-text-secondary">
            {status.current.playerCount}
            {status.server.maxPlayers ? `/${status.server.maxPlayers}` : ""} {t("players").toLowerCase()}
          </p>
          <ActivePlayers names={status.activePlayers} noneLabel={t("noActivePlayers")} />
        </CardContent>
      </Card>
    </>
  );
}

// Operator-facing resource telemetry (CPU/memory/network/uptime, and the
// history charts behind them) — demoted below everything player-facing
// (weekly summary, insights, world) and collapsed by default, matching
// game-server-monitor's own web app: this is "for whoever runs the box,
// not the squad" (a non-technical player never needs to see cgroup
// numbers to know who's online or what they've built).
function ContainerTelemetryDetails({
  status, locale, hours, setHours, graph,
}: {
  status: Extract<AnyServerStatus, { kind: "container" }>["status"];
  locale: string;
  hours: (typeof RANGES)[number]; setHours: (h: (typeof RANGES)[number]) => void;
  graph: Graph | null;
}) {
  const t = useTranslations("games");

  return (
    <details className="mb-6 rounded-lg border border-border bg-bg-surface">
      <summary className="cursor-pointer select-none p-4 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("technicalLabel")}
      </summary>
      <div className="border-t border-border p-4">
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label={t("cpu")} value={formatCpuPercent(status.current.cpuPercent)} />
          <Stat label={t("memory")} value={formatBytes(status.current.memoryBytes)} />
          <Stat
            label={t("uptime")}
            value={formatUptime(uptimeSecondsFromContainer(status.current), locale)}
          />
          <Stat label={t("networkRx")} value={formatRate(status.current.networkRxBps)} />
          <Stat label={t("networkTx")} value={formatRate(status.current.networkTxBps)} />
        </div>

        <div className="mb-3 flex items-center justify-between">
          <p className="font-mono text-[10px] uppercase tracking-wide text-text-muted">
            {t("performanceHistoryLabel")}
          </p>
          <RangeToggle hours={hours} setHours={setHours} />
        </div>

        {graph && graph.data.length > 0 ? (
          <div className="flex flex-col gap-5">
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
          </div>
        ) : (
          <p className="font-mono text-[12.5px] text-text-muted">{t("graphUnavailable")}</p>
        )}
      </div>
    </details>
  );
}

function LocalSaveServerView({
  status, locale, hours, setHours, progress, saveHistory,
}: {
  status: Extract<AnyServerStatus, { kind: "local-save" }>["status"];
  id: string; locale: string;
  hours: (typeof RANGES)[number]; setHours: (h: (typeof RANGES)[number]) => void;
  progress: GameProgress | null; saveHistory: SaveHistory | null;
}) {
  const t = useTranslations("games");

  return (
    <>
      <div className="mb-6 flex items-center gap-2.5">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: status.server.color }}
        />
        <h1 className="text-[26px] font-medium tracking-tight">{status.server.displayName}</h1>
        <Badge variant="designed">{t("localSaveBadge")}</Badge>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label={t("lastUploaded")} value={formatDateTime(status.lastUploadedAt, locale)} />
        {status.lastPlayedAt !== undefined && (
          <Stat label={t("lastSample")} value={formatDateTime(status.lastPlayedAt, locale)} />
        )}
        {status.totalPlaytimeSeconds !== undefined && (
          <Stat label={t("playtime")} value={formatDurationMs(status.totalPlaytimeSeconds * 1000, locale)} />
        )}
        {progress && (
          <Stat label={t("players")} value={String(progress.playerCount)} />
        )}
      </div>

      {progress && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("progressLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-3 p-4 pt-0 sm:grid-cols-3">
            <Stat label={t("progressGourds")} value={String(progress.completionCounts.gourds)} />
            <Stat label={t("progressBigKeys")} value={String(progress.completionCounts.bigKeys)} />
            <Stat label={t("progressFmStations")} value={String(progress.completionCounts.fmStations)} />
            <Stat label={t("progressLookoutLights")} value={String(progress.completionCounts.lookoutLights)} />
            <Stat label={t("progressNamedDoors")} value={String(progress.completionCounts.namedDoors)} />
            <Stat label={t("progressUuidFlags")} value={String(progress.completionCounts.uuidFlags)} />
          </CardContent>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>{t("saveHistoryLabel")}</CardTitle>
          <RangeToggle hours={hours} setHours={setHours} />
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {saveHistory && saveHistory.data.length > 0 ? (
            <ChartBlock label={t("progressLabel")}>
              <LineChart
                series={[
                  {
                    label: t("progressGourds"), color: "var(--accent)",
                    points: saveHistory.data.map((p): ChartPoint => ({
                      timestamp: p.uploadedAt, value: p.completedGourds,
                    })),
                  },
                  {
                    label: t("progressBigKeys"), color: "#e07a5f",
                    points: saveHistory.data.map((p): ChartPoint => ({
                      timestamp: p.uploadedAt, value: p.completedBigKeys,
                    })),
                  },
                ]}
                formatValue={(v) => String(Math.round(v))}
                formatTime={(ts) => formatDateTime(ts, locale)}
                yMin={0}
              />
            </ChartBlock>
          ) : (
            <p className="font-mono text-[12.5px] text-text-muted">{t("graphUnavailable")}</p>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function uptimeSecondsFromContainer(current: { timestamp: number; startedAt?: string }): number {
  if (!current.startedAt) return 0;
  const started = new Date(current.startedAt).getTime();
  if (Number.isNaN(started)) return 0;
  return Math.max(0, Math.floor((current.timestamp - started) / 1000));
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

function ActivePlayers({ names, noneLabel }: { names: string[]; noneLabel: string }) {
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
                        className="flex items-center gap-2 text-text-primary hover:underline"
                      >
                        <PersonAvatar seed={entry.playerKey} size={20} className="rounded-full overflow-hidden" />
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

function AbioticWorldPanels({ world, locale }: { world: WorldState; locale: string }) {
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

/**
 * Big Walk's completion values are opaque internal save-flag ids, not counts
 * or scores (e.g. `gourdBlindfoldFishtrap: 1542` vs `gourdEasySimPress: 153`)
 * — only presence/nonzero-ness is meaningful. Nobody has curated real names
 * for these ids the way Abiotic's `itemLabels.ts` does, so this de-slugs the
 * internal id into an honest, generic label instead of inventing lore.
 */
function deslugBigWalkId(id: string, prefixes: string[]): string {
  let rest = id;
  for (const prefix of prefixes) {
    if (rest.toLowerCase().startsWith(prefix.toLowerCase())) {
      rest = rest.slice(prefix.length);
      break;
    }
  }
  const spaced = rest.replace(/([a-z0-9])([A-Z])/g, "$1 $2").trim();
  return spaced.length > 0 ? spaced : id;
}

function ChecklistChip({ label, done }: { label: string; done: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px]",
        done ? "border-transparent bg-success/15 text-success" : "border-border-strong text-text-muted",
      )}
    >
      {label}
    </span>
  );
}

const CHECKLIST_CATEGORIES = [
  { key: "bigKeys", labelKey: "progressBigKeys", prefixes: ["bigKey"] },
  { key: "namedDoors", labelKey: "progressNamedDoors", prefixes: [] },
  { key: "lookoutLights", labelKey: "progressLookoutLights", prefixes: ["LookoutLight"] },
  { key: "fmStations", labelKey: "progressFmStations", prefixes: ["FmStation"] },
] as const;

function BigWalkWorldPanels({ world, locale }: { world: BigWalkWorld; locale: string }) {
  const t = useTranslations("games");
  void locale;

  const noteEntries = Object.entries(world.notes);
  const gourdEntries = Object.entries(world.completions.gourds).sort(([a], [b]) => a.localeCompare(b));
  const gourdsDone = gourdEntries.filter(([, v]) => v > 0).length;
  const checklistGroups = CHECKLIST_CATEGORIES.map((category) => ({
    ...category,
    entries: Object.entries(world.completions[category.key]).sort(([a], [b]) => a.localeCompare(b)),
  })).filter((group) => group.entries.length > 0);

  return (
    <>
      {checklistGroups.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("bigWalkChecklistLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4 p-4 pt-0">
            {checklistGroups.map((group) => (
              <div key={group.key}>
                <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                  {t(group.labelKey)}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {group.entries.map(([id, value]) => (
                    <ChecklistChip key={id} label={deslugBigWalkId(id, [...group.prefixes])} done={value > 0} />
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {gourdEntries.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("progressGourds")}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            <p className="mb-3 text-[15px] font-medium text-text-primary">{gourdsDone}</p>
            <details>
              <summary className="cursor-pointer select-none font-mono text-[10px] uppercase tracking-wide text-text-muted">
                {t("bigWalkShowAllGourds", { count: gourdEntries.length })}
              </summary>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {gourdEntries.map(([id, value]) => (
                  <ChecklistChip key={id} label={deslugBigWalkId(id, ["gourd"])} done={value > 0} />
                ))}
              </div>
            </details>
          </CardContent>
        </Card>
      )}

      {world.players.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("bigWalkCrewLabel")}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2.5 p-4 pt-0">
            {world.players.map((p) => (
              <div key={p.steamId} className="flex items-center gap-2.5">
                <PersonAvatar
                  seed={p.linkedPerson?.personId ?? p.steamId}
                  size={28}
                  className="rounded-full overflow-hidden"
                />
                {p.linkedPerson ? (
                  <span className="text-[13px] text-text-secondary">{p.linkedPerson.displayName}</span>
                ) : (
                  <span className="font-mono text-[12px] text-text-muted">{p.steamId}</span>
                )}
                {p.walkingProven && (
                  <Badge variant="designed" className="ml-auto">
                    {t("bigWalkWalkingProven")}
                  </Badge>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {noteEntries.length > 0 && (
        <Card className="mb-6">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
              {t("bigWalkNotes")}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 p-4 pt-0">
            {noteEntries.map(([key, value]) => (
              <p key={key} className="text-[12.5px] text-text-secondary">
                {value}
              </p>
            ))}
          </CardContent>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("worldLabel")}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 p-4 pt-0">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label={t("worldSeed")} value={String(world.worldSeed)} />
            <Stat label={t("bigWalkInventory")} value={String(world.inventory.length)} />
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-text-muted">
            <span>{t("bigWalkSlotName", { name: world.slotName })}</span>
            {/* A literal readout of the raw field, not an interpretation —
                nothing in Pulse documents what "skip aids" means in-game,
                so this states the flag rather than asserting an "assist
                on/off" meaning it hasn't earned. */}
            <Badge variant="designed">
              {t(world.skipAidsActive ? "bigWalkSkipAidsOn" : "bigWalkSkipAidsOff")}
            </Badge>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
