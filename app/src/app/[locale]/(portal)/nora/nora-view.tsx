"use client";

import Image from "next/image";
import type { ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  ArrowRight,
  BellRing,
  BookOpenText,
  CircleAlert,
  CircleCheck,
  Clock,
  NotebookPen,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Link } from "@/i18n/routing";
import { art } from "@/components/nora/art";
import { PageFrame } from "@/components/shell/app-shell";
import { useNoraState } from "@/components/shell/nora-presence";
import { useRegisterTitle } from "@/components/shell/page-title";
import { Wordmark } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Panel, PanelHeader, Rows } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { UnavailableState } from "@/components/ui/states";
import { useDoctrine, useMemory, useSkills, useStatus, useTools } from "@/lib/queries";
import { formatDuration, formatRelative, formatTime, formatWeekdayDate } from "@/lib/format";
import { cn } from "@/lib/cn";
import { useNow } from "@/hooks/use-now";

const PLATFORM: Record<string, string> = {
  signal: "Signal",
  whatsapp: "WhatsApp",
  discord: "Discord",
  instagram: "Instagram",
};

// Nora's own page: who she is, whether she is up, what she is holding for
// the rooms, and the three ways into her (what she remembers about you, how
// she behaves, what she can do). Her portrait follows the lighting, as on
// the site: the studio portrait by day, its late-night twin by night.
export function NoraView() {
  const t = useTranslations("noraPage");
  const state = useNoraState();
  const ref = useRegisterTitle("Nora");

  return (
    <PageFrame width="wide">
      <section
        aria-labelledby="nora-title"
        className="mb-6 grid grid-cols-[112px_minmax(0,1fr)] items-center gap-x-5 gap-y-5 sm:grid-cols-[160px_minmax(0,1fr)] lg:mb-10 lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-x-12"
      >
        <figure className="row-span-2 self-start lg:row-span-3">
          <div className="relative aspect-[2/3] overflow-hidden rounded-panel bg-hoodie shadow-[0_0_0_1px_var(--hairline)] lg:rounded-sheet">
            <Image
              src={art.canonical}
              alt={t("portraitAlt")}
              priority
              sizes="(min-width: 1024px) 280px, 160px"
              className="size-full object-cover dark:hidden"
            />
            <Image
              src={art.nightPortrait}
              alt={t("portraitNightAlt")}
              sizes="(min-width: 1024px) 280px, 160px"
              className="hidden size-full object-cover dark:block"
            />
          </div>
          <figcaption className="mt-3 hidden items-center gap-3 font-mono text-meta text-ink-muted before:h-px before:w-7 before:bg-seam lg:flex">
            {t("caption")}
          </figcaption>
        </figure>

        <div className="grid content-end gap-2 self-end lg:self-auto">
          <p className="inline-flex w-fit items-center gap-2 rounded-full border border-hairline bg-surface py-1 pr-3 pl-2.5 text-meta font-medium text-ink-soft">
            <span
              className={cn(
                "size-2 rounded-full",
                state === "listening" ? "bg-blush" : "border-[1.5px] border-seam",
              )}
              aria-hidden="true"
            />
            {t(`state.${state}`)}
          </p>
          <h1 id="nora-title" ref={ref} className="leading-none">
            <Wordmark className="text-[2.75rem] sm:text-[3.5rem] lg:text-[4.5rem]" />
          </h1>
        </div>

        <div className="col-span-2 grid gap-3 sm:col-span-1 lg:col-start-2">
          <p className="max-w-[56ch] text-lead text-ink-soft">{t("lede")}</p>
          <p className="max-w-[56ch] text-body text-ink-muted">{t("aside")}</p>
        </div>

        <div className="col-span-2 lg:col-span-1 lg:col-start-2">
          <HowSheIs />
        </div>
      </section>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-6">
        <Reminders />
        <WaysIn />
      </div>
    </PageFrame>
  );
}

// ---------------------------------------------------------------- health

function HowSheIs() {
  const t = useTranslations("noraPage");
  const locale = useLocale();
  const status = useStatus();

  if (status.isPending) return <Skeleton className="h-[148px] rounded-panel" />;
  if (status.isError && !status.data) {
    return (
      <Panel>
        <UnavailableState compact what={t("statusUnavailable")} onRetry={() => status.refetch()} />
      </Panel>
    );
  }
  const s = status.data;
  return (
    <Panel aria-labelledby="health-title">
      <h2 id="health-title" className="sr-only">
        {t("health")}
      </h2>
      <dl className="grid divide-y divide-hairline sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <HealthItem
          label={t("record")}
          ok={s.store.reachable}
          value={s.store.reachable ? t("recordOk") : t("recordDown")}
        />
        <HealthItem
          label={t("memory")}
          ok={s.honcho.reachable}
          value={s.honcho.reachable ? t("memoryOk") : t("memoryDown")}
        />
        <div className="grid content-start gap-1 p-4 sm:p-5">
          <dt className="text-label font-medium text-ink-muted">{t("uptime")}</dt>
          <dd className="flex items-center gap-1.5 text-[0.9375rem] font-semibold text-ink">
            <Clock className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
            <span className="font-mono tnum">{formatDuration(s.uptime_seconds * 1000, locale)}</span>
          </dd>
          <dd className="text-meta text-ink-muted">{t("uptimeLede")}</dd>
        </div>
      </dl>
    </Panel>
  );
}

function HealthItem({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  const Icon = ok ? CircleCheck : CircleAlert;
  return (
    <div className="grid content-start gap-1 p-4 sm:p-5">
      <dt className="text-label font-medium text-ink-muted">{label}</dt>
      <dd className="flex items-start gap-1.5 text-[0.9375rem] font-semibold text-ink">
        <Icon className={cn("mt-0.5 size-4 shrink-0", ok ? "text-success" : "text-danger")} aria-hidden="true" />
        <span>{value}</span>
      </dd>
    </div>
  );
}

// ------------------------------------------------------------- reminders

function Reminders() {
  const t = useTranslations("noraPage");
  const locale = useLocale();
  const now = useNow();
  const status = useStatus();
  const r = status.data?.reminders;

  return (
    <Panel id="reminders" aria-labelledby="reminders-title" className="scroll-mt-24">
      <PanelHeader
        id="reminders-title"
        title={t("reminders")}
        description={
          r
            ? r.count === 0
              ? t("remindersNone")
              : t("remindersCount", { count: r.count })
            : undefined
        }
      />
      {status.isPending ? (
        <div className="grid gap-2 px-4 pb-4 sm:px-5">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : !r ? (
        <UnavailableState compact onRetry={() => status.refetch()} />
      ) : r.upcoming.length === 0 ? (
        <p className="flex items-center gap-2 px-4 pb-5 text-body text-ink-soft sm:px-5">
          <BellRing className="size-4 text-ink-muted" aria-hidden="true" />
          {t("remindersEmpty")}
        </p>
      ) : (
        <>
          <Rows className="border-t border-hairline">
            {r.upcoming.map((u, i) => {
              const at = Date.parse(u.due_at);
              return (
                <li key={`${u.due_at}-${i}`} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                  <span className="grid size-9 shrink-0 place-items-center rounded-control bg-hoodie text-ink-soft">
                    <BellRing className="size-4" aria-hidden="true" />
                  </span>
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <span className="text-[0.9375rem] font-semibold text-ink">
                      {formatWeekdayDate(at, locale)} · <span className="font-mono tnum">{formatTime(at, locale)}</span>
                    </span>
                    <span className="text-meta text-ink-muted">{now ? formatRelative(at, locale, now) : " "}</span>
                  </span>
                  <Badge tone="outline">{PLATFORM[u.platform] ?? u.platform}</Badge>
                </li>
              );
            })}
          </Rows>
          {r.count > r.upcoming.length ? (
            <p className="border-t border-hairline px-4 py-3 text-meta text-ink-muted sm:px-5">
              {t("remindersMore", { count: r.count - r.upcoming.length })}
            </p>
          ) : null}
        </>
      )}
      <p className="border-t border-hairline px-4 py-3 text-label text-ink-muted sm:px-5">{t("remindersPrivacy")}</p>
    </Panel>
  );
}

// --------------------------------------------------------------- ways in

function WaysIn() {
  const t = useTranslations("noraPage");
  const tNav = useTranslations("nav");
  const memory = useMemory();
  const doctrine = useDoctrine();
  const tools = useTools();
  const skills = useSkills();

  const episodes = memory.data?.[0]?.episodes.length;
  const count = (n: number | undefined, pending: boolean): ReactNode =>
    pending ? <Skeleton className="h-3.5 w-24" /> : n === undefined ? t("countUnknown") : null;

  return (
    <nav aria-label={t("waysIn")} className="grid gap-3">
      <WayIn
        href="/nora/memory"
        icon={NotebookPen}
        title={tNav("memory")}
        lede={t("memoryLede")}
        meta={
          count(episodes, memory.isPending) ??
          t("memoryCount", { n: episodes ?? 0 })
        }
      />
      <WayIn
        href="/nora/doctrine"
        icon={BookOpenText}
        title={tNav("doctrine")}
        lede={t("doctrineLede")}
        meta={
          count(doctrine.data?.length, doctrine.isPending) ??
          t("doctrineCount", { n: doctrine.data?.length ?? 0 })
        }
      />
      <WayIn
        href="/nora/abilities"
        icon={Wrench}
        title={tNav("abilities")}
        lede={t("abilitiesLede")}
        meta={
          count(tools.data?.length, tools.isPending) ??
          t("abilitiesCount", { tools: tools.data?.length ?? 0, skills: skills.data?.length ?? 0 })
        }
      />
    </nav>
  );
}

function WayIn({
  href,
  icon: Icon,
  title,
  lede,
  meta,
}: {
  href: string;
  icon: LucideIcon;
  title: string;
  lede: string;
  meta: ReactNode;
}) {
  return (
    <Link
      href={href}
      className="group flex items-start gap-4 rounded-panel border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-seam sm:p-5"
    >
      <span className="grid size-10 shrink-0 place-items-center rounded-control bg-hoodie text-ink-soft">
        <Icon className="size-[18px]" aria-hidden="true" />
      </span>
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="text-[0.9375rem] font-semibold text-ink">{title}</span>
        <span className="text-meta text-ink-soft">{lede}</span>
        <span className="text-label text-ink-muted tnum">{meta}</span>
      </span>
      <ArrowRight
        className="mt-1 size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5"
        aria-hidden="true"
      />
    </Link>
  );
}
