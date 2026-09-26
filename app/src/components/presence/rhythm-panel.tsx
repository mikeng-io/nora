"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useRhythm } from "@/lib/queries";
import { formatHours, formatNumber, intlLocale, zonedParts } from "@/lib/format";
import { useNow } from "@/hooks/use-now";
import { WeekHeatmap } from "@/components/charts/week-heatmap";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";

const WINDOWS = [30, 60, 90] as const;
type Metric = "dwell" | "stays";

/** Monday first, in the reader's language: the bins' own order. */
export function weekdayLabels(locale: string, style: "short" | "narrow" = "short"): string[] {
  // 2026-09-28 is a Monday.
  return Array.from({ length: 7 }, (_, i) =>
    new Intl.DateTimeFormat(intlLocale(locale), { weekday: style, timeZone: "UTC" }).format(Date.UTC(2026, 8, 28 + i)),
  );
}

// One person's week: when they are usually settled somewhere, hour by hour,
// from the stays Nora has seen (hourOfWeek 0 is Monday 00:00, Hong Kong
// time). The hour it is now is outlined, so "is this usual?" has an answer.
export function RhythmPanel({ subjectId, name }: { subjectId: string; name: string }) {
  const t = useTranslations("patterns");
  const locale = useLocale();
  const now = useNow();
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const [metric, setMetric] = useState<Metric>("dwell");
  const rhythm = useRhythm(subjectId, days);

  const cells = useMemo(
    () =>
      (rhythm.data?.bins ?? []).map((b) => ({
        day: Math.floor(b.hourOfWeek / 24),
        hour: b.hourOfWeek % 24,
        value: metric === "dwell" ? b.totalDwellMs : b.stayCount,
      })),
    [rhythm.data, metric],
  );
  const labels = weekdayLabels(locale);
  const fmt = (v: number) => (metric === "dwell" ? formatHours(v, locale) : t("rhythm.stays", { count: v }));
  const peak = cells.reduce<(typeof cells)[number] | null>((a, c) => (!a || c.value > a.value ? c : a), null);
  const nowParts = now ? zonedParts(now) : null;
  const nowCell = nowParts ? cells.find((c) => c.day === nowParts.weekday && c.hour === nowParts.hour) : null;
  const total = cells.reduce((s, c) => s + c.value, 0);
  const typicalNow =
    nowCell && peak && peak.value > 0 ? (nowCell.value / peak.value >= 0.5 ? "usual" : nowCell.value > 0 ? "sometimes" : "rare") : null;

  return (
    <Panel>
      <PanelHeader
        title={t("rhythm.title")}
        description={t("rhythm.lede", { name })}
        action={
          <Segmented
            size="sm"
            label={t("window")}
            value={days}
            onChange={setDays}
            options={WINDOWS.map((d) => ({ value: d, label: t("days", { count: d }) }))}
          />
        }
      />
      <PanelBody className="grid gap-5">
        {rhythm.isPending ? (
          <Skeleton className="h-[220px]" />
        ) : rhythm.isError && !rhythm.data ? (
          <UnavailableState compact onRetry={() => rhythm.refetch()} />
        ) : total === 0 ? (
          <EmptyState compact>{t("rhythm.empty", { name })}</EmptyState>
        ) : (
          <>
            <Segmented
              size="sm"
              label={t("rhythm.metric")}
              value={metric}
              onChange={setMetric}
              options={[
                { value: "dwell", label: t("rhythm.dwell") },
                { value: "stays", label: t("rhythm.staysLabel") },
              ]}
              className="w-fit"
            />
            <WeekHeatmap
              cells={cells}
              dayLabels={labels}
              formatValue={fmt}
              now={nowParts ? { day: nowParts.weekday, hour: nowParts.hour } : null}
              label={t("rhythm.chartLabel", {
                name,
                day: peak ? labels[peak.day] : "",
                hour: peak ? String(peak.hour).padStart(2, "0") : "",
              })}
            />
            <dl className="grid gap-3 border-t border-hairline pt-4 sm:grid-cols-2">
              {peak ? (
                <div className="grid gap-0.5">
                  <dt className="text-label text-ink-muted">{t("rhythm.peak")}</dt>
                  <dd className="text-body text-ink">
                    {t("rhythm.peakValue", {
                      day: labels[peak.day],
                      hour: String(peak.hour).padStart(2, "0"),
                      value: fmt(peak.value),
                    })}
                  </dd>
                </div>
              ) : null}
              {typicalNow ? (
                <div className="grid gap-0.5">
                  <dt className="text-label text-ink-muted">{t("rhythm.rightNow")}</dt>
                  <dd className="text-body text-ink">{t(`rhythm.now.${typicalNow}`, { name })}</dd>
                </div>
              ) : null}
            </dl>
            <p className="text-label text-ink-muted">
              {t("rhythm.note", { days: formatNumber(rhythm.data?.windowDays ?? days, locale) })}
            </p>
          </>
        )}
      </PanelBody>
    </Panel>
  );
}
