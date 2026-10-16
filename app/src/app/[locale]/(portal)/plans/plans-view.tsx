"use client";

import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Ban, ChevronRight, MapPin } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Segmented, useTabParam } from "@/components/ui/tabs";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { PlanDate } from "@/components/plans/plan-date";
import { usePlans } from "@/lib/queries";
import type { Plan } from "@/lib/api/people";
import { dateFormat, dayIndex, formatRelative, formatTime, zonedParts } from "@/lib/format";
import { pastPlans, planTime, upcomingPlans } from "@/lib/plans";
import { useNow } from "@/hooks/use-now";
import { cn } from "@/lib/cn";

const VIEWS = ["upcoming", "past"] as const;
type View = (typeof VIEWS)[number];
type Bucket = "today" | "thisWeek" | "nextWeek" | "later" | "undated";

/** Which heading a coming plan sits under, by calendar week (Monday first)
 * in the display zone. */
function bucketOf(p: Plan, now: number): Bucket {
  const at = planTime(p);
  if (at === null) return "undated";
  const d = dayIndex(at) - dayIndex(now);
  if (d <= 0) return "today";
  const toWeekEnd = 6 - zonedParts(now).weekday;
  if (d <= toWeekEnd) return "thisWeek";
  if (d <= toWeekEnd + 7) return "nextWeek";
  return "later";
}

// Plans are made in the group chat; this is where they are looked up. What
// is coming, by week, on one thin line; what is past, by month, quieter.
export function PlansView() {
  const t = useTranslations("plans");
  const locale = useLocale();
  const now = useNow() || Date.now();
  const plans = usePlans();
  const [view, setView] = useTabParam<View>(VIEWS, "upcoming", "show");

  const upcoming = useMemo(() => upcomingPlans(plans.data ?? [], now), [plans.data, now]);
  const past = useMemo(() => pastPlans(plans.data ?? [], now), [plans.data, now]);

  const groups = useMemo(() => {
    if (view === "upcoming") {
      const order: Bucket[] = ["today", "thisWeek", "nextWeek", "later", "undated"];
      return order
        .map((b) => ({ key: b, title: t(`bucket.${b}`), plans: upcoming.filter((p) => bucketOf(p, now) === b) }))
        .filter((g) => g.plans.length);
    }
    const byMonth = new Map<string, Plan[]>();
    for (const p of past) {
      const at = planTime(p) ?? Date.parse(p.created_at);
      const key = dateFormat(locale, { year: "numeric", month: "long" }).format(at);
      byMonth.set(key, [...(byMonth.get(key) ?? []), p]);
    }
    return [...byMonth].map(([key, ps]) => ({ key, title: key, plans: ps }));
  }, [view, upcoming, past, now, locale, t]);

  return (
    <PageFrame width="narrow">
      <PageHeader
        title={t("title")}
        description={t("lede")}
        actions={
          <Segmented
            label={t("view")}
            value={view}
            onChange={(v) => setView(v)}
            options={[
              { value: "upcoming", label: t("upcomingN", { count: upcoming.length }) },
              { value: "past", label: t("past") },
            ]}
          />
        }
      />
      {plans.isPending ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[76px] rounded-panel" />
          ))}
        </div>
      ) : plans.isError && !plans.data ? (
        <UnavailableState onRetry={() => plans.refetch()} />
      ) : groups.length === 0 ? (
        <EmptyState art={view === "upcoming" ? "notebook" : "quiet"} title={t(view === "upcoming" ? "emptyTitle" : "emptyPastTitle")}>
          {t(view === "upcoming" ? "empty" : "emptyPast")}
        </EmptyState>
      ) : (
        <div className="grid gap-8" key={view}>
          {groups.map((g, i) => (
            <section key={g.key} aria-labelledby={`plans-group-${i}`} className="page-enter">
              <h2 id={`plans-group-${i}`} className="mb-3 text-label font-medium text-ink-muted">
                {g.title}
              </h2>
              <ol className="relative grid gap-2.5">
                {/* The cable: one line through the dates. */}
                <span className="absolute top-6 bottom-6 left-[23px] w-px bg-hairline" aria-hidden="true" />
                {g.plans.map((p) => (
                  <PlanRow key={p.id} plan={p} now={now} past={view === "past"} />
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </PageFrame>
  );
}

function PlanRow({ plan: p, now, past }: { plan: Plan; now: number; past: boolean }) {
  const t = useTranslations("plans");
  const locale = useLocale();
  const at = planTime(p);
  const cancelled = p.status === "cancelled";
  const venue = p.venue.name ?? p.venue.text;
  return (
    <li className="relative">
      <Link
        href={`/plans/${p.id}`}
        className="group relative flex items-center gap-4 rounded-panel border border-hairline bg-surface p-3 pr-4 transition-colors duration-100 hover:border-seam"
      >
        <PlanDate when={p.when} muted={past} />
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="flex items-center gap-2">
            <span className={cn("truncate text-[0.9375rem] font-semibold", cancelled ? "text-ink-muted line-through" : "text-ink")}>
              {p.title}
            </span>
          </span>
          <span className="flex min-w-0 items-center gap-1.5 text-meta text-ink-muted">
            {at !== null ? <span className="font-mono tnum">{formatTime(at, locale)}</span> : <span>{t("noDate")}</span>}
            {venue ? (
              <>
                <span aria-hidden="true">·</span>
                <MapPin className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">{venue}</span>
              </>
            ) : null}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          {cancelled ? (
            <Badge tone="danger" icon={<Ban aria-hidden="true" />}>
              {t("status.cancelled")}
            </Badge>
          ) : p.status !== "confirmed" ? (
            <Badge>{t(`status.${p.status}`)}</Badge>
          ) : null}
          {!past && at !== null && !cancelled ? (
            <span className="text-label text-ink-muted">{formatRelative(at, locale, now)}</span>
          ) : null}
        </span>
        <ChevronRight
          className="hidden size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5 sm:block"
          aria-hidden="true"
        />
      </Link>
    </li>
  );
}
