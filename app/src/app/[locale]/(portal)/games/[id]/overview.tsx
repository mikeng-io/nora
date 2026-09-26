"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronDown, Users } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat, StatGrid } from "@/components/ui/stat";
import { TimeChart } from "@/components/charts/time-chart";
import { PlayerChip } from "@/components/games/game-bits";
import type { LocalSaveStatus, ServerStatus } from "@/lib/api/games";
import { useProgress, useSaveHistory, useServerGraph, useWeekly } from "@/lib/queries";
import { uptimeMs } from "@/lib/games";
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatHours,
  formatNumber,
  formatPercent,
  formatRate,
  formatTime,
  formatWeekday,
} from "@/lib/format";
import { cn } from "@/lib/cn";

const RANGES = [24, 72, 168] as const;

function useRangeFormatters(hours: number) {
  const locale = useLocale();
  return {
    formatTime: (t: number) => formatDateTime(t, locale),
    formatTick: (t: number) => (hours <= 24 ? formatTime(t, locale) : formatWeekday(t, locale)),
  };
}

// ------------------------------------------------------------ container

export function ContainerOverview({ id, status }: { id: string; status: ServerStatus }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [hours, setHours] = useState<(typeof RANGES)[number]>(24);
  const graph = useServerGraph(id, hours);
  const weekly = useWeekly(id);
  const fmt = useRangeFormatters(hours);
  const players = status.activeProfiles ?? status.activePlayers.map((n) => ({ playerKey: "", playerName: n }));
  const max = status.server.maxPlayers;

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        <Panel aria-labelledby="now-title">
          <PanelHeader
            id="now-title"
            title={t("onNow")}
            description={
              status.current.online
                ? t("playersNow", { count: status.current.playerCount, max: max ?? 0, hasMax: max ? "yes" : "no" })
                : t("nobodyOffline")
            }
          />
          <PanelBody>
            {players.length ? (
              <ul className="flex flex-wrap gap-2">
                {players.map((p, i) => (
                  <li key={p.playerKey || i}>
                    {p.playerKey ? (
                      <Link href={`/games/${id}/players/${encodeURIComponent(p.playerKey)}`} className="rounded-full">
                        <PlayerChip name={p.playerName} online />
                      </Link>
                    ) : (
                      <PlayerChip name={p.playerName} online />
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="flex items-center gap-2 text-body text-ink-soft">
                <Users className="size-4 text-ink-muted" aria-hidden="true" />
                {t("nobodyOn")}
              </p>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="players-chart-title">
          <PanelHeader
            id="players-chart-title"
            title={t("playersOverTime")}
            action={
              <Segmented
                size="sm"
                label={t("range")}
                value={hours}
                onChange={setHours}
                options={RANGES.map((h) => ({ value: h, label: t(`rangeN.${h}`) }))}
              />
            }
          />
          <PanelBody>
            {graph.isPending ? (
              <Skeleton className="h-[168px]" />
            ) : graph.data && graph.data.data.length > 1 ? (
              <TimeChart
                points={graph.data.data.map((p) => ({ t: p.timestamp, v: p.playerMax }))}
                step
                label={t("playersOverTime")}
                formatValue={(v) => formatNumber(v, locale)}
                {...fmt}
              />
            ) : (
              <p className="text-body text-ink-soft">{t("noHistory")}</p>
            )}
          </PanelBody>
        </Panel>

        <Telemetry id={id} status={status} hours={hours} />
      </div>

      <div className="grid gap-4 lg:gap-6">
        <Panel aria-labelledby="week-title">
          <PanelHeader id="week-title" title={t("thisWeek")} />
          <PanelBody>
            {weekly.isPending ? (
              <Skeleton className="h-32" />
            ) : weekly.data ? (
              <StatGrid className="sm:grid-cols-2">
                <Stat label={t("weekly.players")} value={formatNumber(weekly.data.uniquePlayers, locale)} />
                <Stat label={t("weekly.sessions")} value={formatNumber(weekly.data.sessions, locale)} />
                <Stat label={t("weekly.playtime")} value={formatHours(weekly.data.playtimeMs, locale)} />
                <Stat label={t("weekly.peak")} value={formatNumber(weekly.data.peakPlayers, locale)} />
                <Stat label={t("weekly.deaths")} value={formatNumber(weekly.data.deaths, locale)} />
                <Stat label={t("weekly.availability")} value={formatPercent(weekly.data.availabilityPercent / 100, locale)} />
              </StatGrid>
            ) : (
              <p className="text-body text-ink-soft">{t("noWeekly")}</p>
            )}
          </PanelBody>
        </Panel>
      </div>
    </div>
  );
}

/** The box's own numbers, folded away: they matter to whoever runs it, not
 * to someone checking who is on. */
function Telemetry({ id, status, hours }: { id: string; status: ServerStatus; hours: number }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [measure, setMeasure] = useState<"cpu" | "memory">("cpu");
  const graph = useServerGraph(id, hours, open);
  const fmt = useRangeFormatters(hours);
  const c = status.current;
  const up = uptimeMs(c);

  return (
    <Panel>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-4 py-4 text-left sm:px-5"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-title font-semibold text-ink">{t("telemetry")}</span>
          <span className="block text-meta text-ink-muted">{t("telemetryLede")}</span>
        </span>
        <ChevronDown
          className={cn("size-5 shrink-0 text-ink-muted transition-transform duration-160 ease-nora", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div className="grid gap-6 border-t border-hairline px-4 py-5 animate-fade sm:px-5">
          <StatGrid>
            <Stat label={t("cpu")} value={`${formatNumber(c.cpuPercent, locale, 1)}%`} />
            <Stat label={t("memory")} value={formatBytes(c.memoryBytes)} hint={t("ofLimit", { limit: formatBytes(c.memoryLimitBytes) })} />
            <Stat label={t("download")} value={formatRate(c.networkRxBps)} />
            <Stat label={t("upload")} value={formatRate(c.networkTxBps)} />
            <Stat label={t("uptime")} value={up !== null ? formatDuration(up, locale) : "—"} />
            <Stat label={t("restarts")} value={formatNumber(c.restartCount, locale)} />
            <Stat label={t("processes")} value={formatNumber(c.pids, locale)} />
            <Stat label={t("containerStatus")} value={c.status} />
          </StatGrid>
          <div className="grid gap-3">
            <Segmented
              size="sm"
              label={t("measure")}
              value={measure}
              onChange={setMeasure}
              options={[
                { value: "cpu", label: t("cpu") },
                { value: "memory", label: t("memory") },
              ]}
              className="w-fit"
            />
            {graph.data && graph.data.data.length > 1 ? (
              <TimeChart
                points={graph.data.data.map((p) => ({ t: p.timestamp, v: measure === "cpu" ? p.cpuAvg : p.memoryAvg }))}
                yMax={measure === "cpu" ? 100 : undefined}
                label={measure === "cpu" ? t("cpu") : t("memory")}
                formatValue={(v) => (measure === "cpu" ? `${formatNumber(v, locale)}%` : formatBytes(v))}
                {...fmt}
              />
            ) : graph.isPending ? (
              <Skeleton className="h-[168px]" />
            ) : (
              <p className="text-body text-ink-soft">{t("noHistory")}</p>
            )}
          </div>
          {c.image ? <p className="font-mono text-label break-all text-ink-muted">{c.image}</p> : null}
        </div>
      ) : null}
    </Panel>
  );
}

// ----------------------------------------------------------- local save

const PROGRESS_KEYS = ["gourds", "bigKeys", "fmStations", "lookoutLights", "namedDoors"] as const;
type ProgressKey = (typeof PROGRESS_KEYS)[number];
const HISTORY_FIELD: Record<ProgressKey, "completedGourds" | "completedBigKeys" | "completedFmStations" | "completedLookoutLights" | "completedNamedDoors"> = {
  gourds: "completedGourds",
  bigKeys: "completedBigKeys",
  fmStations: "completedFmStations",
  lookoutLights: "completedLookoutLights",
  namedDoors: "completedNamedDoors",
};

export function LocalSaveOverview({ id, status }: { id: string; status: LocalSaveStatus }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [hours, setHours] = useState<(typeof RANGES)[number]>(168);
  const [measure, setMeasure] = useState<ProgressKey>("gourds");
  const progress = useProgress(id);
  const history = useSaveHistory(id, hours);
  const fmt = useRangeFormatters(hours);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        <Panel aria-labelledby="save-title">
          <PanelHeader id="save-title" title={t("theSave")} description={t("theSaveLede")} />
          <PanelBody>
            <StatGrid>
              {status.lastPlayedAt ? <Stat label={t("lastPlayedLabel")} value={formatDateTime(status.lastPlayedAt, locale)} mono={false} /> : null}
              <Stat label={t("lastUploaded")} value={formatDateTime(status.lastUploadedAt, locale)} mono={false} />
              {status.totalPlaytimeSeconds !== undefined ? (
                <Stat label={t("playtime")} value={formatHours(status.totalPlaytimeSeconds * 1000, locale)} />
              ) : null}
              {progress.data ? <Stat label={t("crew")} value={formatNumber(progress.data.playerCount, locale)} /> : null}
            </StatGrid>
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="progress-chart-title">
          <PanelHeader
            id="progress-chart-title"
            title={t("progressOverTime")}
            action={
              <Segmented
                size="sm"
                label={t("range")}
                value={hours}
                onChange={setHours}
                options={RANGES.map((h) => ({ value: h, label: t(`rangeN.${h}`) }))}
              />
            }
          />
          <PanelBody className="grid gap-3">
            <div className="scrollbar-none -mx-1 overflow-x-auto px-1">
              <Segmented
                size="sm"
                label={t("measure")}
                value={measure}
                onChange={setMeasure}
                options={PROGRESS_KEYS.map((k) => ({ value: k, label: t(`progress.${k}`) }))}
              />
            </div>
            {history.isPending ? (
              <Skeleton className="h-[168px]" />
            ) : history.data && history.data.data.length > 1 ? (
              <TimeChart
                points={history.data.data.map((p) => ({ t: p.uploadedAt, v: p[HISTORY_FIELD[measure]] }))}
                step
                label={t(`progress.${measure}`)}
                formatValue={(v) => formatNumber(v, locale)}
                {...fmt}
              />
            ) : (
              <p className="text-body text-ink-soft">{t("noHistory")}</p>
            )}
          </PanelBody>
        </Panel>
      </div>

      <Panel aria-labelledby="progress-title">
        <PanelHeader id="progress-title" title={t("progressTitle")} />
        <PanelBody>
          {progress.isPending ? (
            <Skeleton className="h-40" />
          ) : progress.data ? (
            <StatGrid className="sm:grid-cols-2">
              {PROGRESS_KEYS.map((k) => (
                <Stat key={k} label={t(`progress.${k}`)} value={formatNumber(progress.data.completionCounts[k], locale)} />
              ))}
              <Stat label={t("progress.uuidFlags")} value={formatNumber(progress.data.completionCounts.uuidFlags, locale)} />
            </StatGrid>
          ) : (
            <p className="text-body text-ink-soft">{t("noProgress")}</p>
          )}
        </PanelBody>
      </Panel>
    </div>
  );
}
