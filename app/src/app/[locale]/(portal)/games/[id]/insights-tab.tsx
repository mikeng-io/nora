"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { BarList } from "@/components/charts/bars";
import { WeekHeatmap } from "@/components/charts/week-heatmap";
import { weekdayLabels } from "@/components/presence/rhythm-panel";
import { useInsights } from "@/lib/queries";
import { formatHours, formatNumber } from "@/lib/format";
import { localized, refName, sundayFirstToMondayFirst } from "@/lib/games";

const WINDOWS = [7, 30, 90] as const;

// Who plays most, when the server is busy, who plays together, what kills
// people, who learns fastest. Every ranking is one series in denim, with
// names as text: identity is the label, never the bar's colour.
export function InsightsTab({ id }: { id: string }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const insights = useInsights(id, days);

  const controls = (
    <Segmented
      label={t("window")}
      value={days}
      onChange={setDays}
      options={WINDOWS.map((d) => ({ value: d, label: t("daysN", { count: d }) }))}
      className="w-fit"
    />
  );

  if (insights.isPending)
    return (
      <div className="grid gap-4">
        {controls}
        <Skeleton className="h-96 rounded-panel" />
      </div>
    );
  if (insights.isError && !insights.data) return <UnavailableState onRetry={() => insights.refetch()} />;
  const d = insights.data;
  const labels = weekdayLabels(locale);

  return (
    <div className={`grid gap-4 transition-opacity lg:gap-6 ${insights.isPlaceholderData ? "opacity-60" : ""}`}>
      {controls}
      <div className="grid items-start gap-4 lg:grid-cols-2 lg:gap-6">
        <Panel aria-labelledby="lb-title">
          <PanelHeader id="lb-title" title={t("leaderboard")} description={t("leaderboardLede", { days })} />
          <PanelBody>
            {d.leaderboard.length ? (
              <BarList
                label={t("leaderboard")}
                data={d.leaderboard.slice(0, 8).map((p, i) => ({
                  key: p.playerKey,
                  label: (
                    <span className="flex items-center gap-2">
                      <span className="w-4 font-mono text-label text-ink-muted tnum">{i + 1}</span>
                      {p.playerName}
                    </span>
                  ),
                  value: p.playtimeMs,
                  display: formatHours(p.playtimeMs, locale),
                  hint: t("sessionsN", { count: p.sessions }),
                }))}
              />
            ) : (
              <EmptyState compact>{t("noData")}</EmptyState>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="heat-title">
          <PanelHeader id="heat-title" title={t("busyHours")} description={t("busyHoursLede")} />
          <PanelBody>
            {d.heatmap.some((c) => c.playerHours > 0) ? (
              <WeekHeatmap
                cells={d.heatmap.map((c) => ({ day: sundayFirstToMondayFirst(c.weekday), hour: c.hour, value: c.playerHours }))}
                dayLabels={labels}
                formatValue={(v) => formatHours(v * 3_600_000, locale)}
                label={t("busyHoursLede")}
              />
            ) : (
              <EmptyState compact>{t("noData")}</EmptyState>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="coplay-title">
          <PanelHeader id="coplay-title" title={t("coplay")} description={t("coplayLede")} />
          <PanelBody>
            {d.coplay.length ? (
              <BarList
                label={t("coplay")}
                data={d.coplay.slice(0, 8).map((c) => ({
                  key: `${c.aKey}-${c.bKey}`,
                  label: `${c.aName} & ${c.bName}`,
                  value: c.overlapMs,
                  display: formatHours(c.overlapMs, locale),
                }))}
              />
            ) : (
              <EmptyState compact>{t("noCoplay")}</EmptyState>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="deaths-title">
          <PanelHeader id="deaths-title" title={t("deathCauses")} />
          <PanelBody>
            {d.deathCauses.length ? (
              <BarList
                label={t("deathCauses")}
                data={d.deathCauses.slice(0, 8).map((c) => ({
                  key: c.cause,
                  label: localized(c.cause, c.causeZhHant, locale),
                  value: c.count,
                  display: formatNumber(c.count, locale),
                }))}
              />
            ) : (
              <EmptyState compact>{t("noDeaths")}</EmptyState>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="kills-title">
          <PanelHeader id="kills-title" title={t("kills")} />
          <PanelBody>
            {d.killLeaderboard.length ? (
              <BarList
                label={t("kills")}
                data={d.killLeaderboard.slice(0, 8).map((k) => ({
                  key: k.playerKey,
                  label: k.playerName,
                  value: k.totalKills,
                  display: formatNumber(k.totalKills, locale),
                  hint: k.kills[0] ? t("mostlyKills", { what: refName(k.kills[0], locale) }) : undefined,
                }))}
              />
            ) : (
              <EmptyState compact>{t("noKills")}</EmptyState>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="learn-title">
          <PanelHeader id="learn-title" title={t("learners")} />
          <PanelBody>
            {d.learnerLeaderboard.length ? (
              <BarList
                label={t("learners")}
                data={d.learnerLeaderboard.slice(0, 8).map((l) => ({
                  key: l.playerKey,
                  label: l.playerName,
                  value: l.xp,
                  display: t("xp", { xp: formatNumber(l.xp, locale) }),
                  hint: localized(l.topSkill.en, l.topSkill.zhHant, locale),
                }))}
              />
            ) : (
              <EmptyState compact>{t("noLearners")}</EmptyState>
            )}
          </PanelBody>
        </Panel>
      </div>
    </div>
  );
}
