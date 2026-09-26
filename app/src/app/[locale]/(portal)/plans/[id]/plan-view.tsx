"use client";

import { useLocale, useTranslations } from "next-intl";
import { Ban, CalendarPlus, Check, CircleHelp, Clock, ExternalLink, MapPin, Share2, Star, X } from "lucide-react";
import { toast } from "sonner";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { useViewer } from "@/components/shell/viewer";
import { PageHeader } from "@/components/ui/page-header";
import { PersonAvatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { isNotFound } from "@/lib/api/client";
import type { PlanDetail, Rsvp } from "@/lib/api/people";
import { usePlan, useRoster } from "@/lib/queries";
import { formatDate, formatLongDate, formatRelative, formatTime } from "@/lib/format";
import { mapsUrl, planTime, planToIcs } from "@/lib/plans";
import { useNow } from "@/hooks/use-now";

// One plan: when, where, who is coming, and the three things you might do
// with it outside the portal: put it in your calendar, open the venue in a
// maps app, send it to someone. Answering happens in the group chat, where
// Nora is; the portal only reads.
export function PlanView({ id }: { id: string }) {
  const t = useTranslations("plans");
  const plan = usePlan(id);

  if (plan.isPending) {
    return (
      <PageFrame width="narrow">
        <Skeleton className="mb-3 h-8 w-2/3" />
        <Skeleton className="mb-8 h-5 w-1/2" />
        <Skeleton className="h-48 rounded-panel" />
      </PageFrame>
    );
  }
  if (plan.isError && !plan.data) {
    return (
      <PageFrame width="narrow">
        {isNotFound(plan.error) ? (
          <EmptyState
            art="lost"
            title={t("notFoundTitle")}
            action={
              <Link href="/plans" className="text-meta font-semibold text-denim hover:underline">
                {t("back")}
              </Link>
            }
          >
            {t("notFound")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => plan.refetch()} />
        )}
      </PageFrame>
    );
  }
  return <PlanDetailView plan={plan.data} />;
}

function PlanDetailView({ plan: p }: { plan: PlanDetail }) {
  const t = useTranslations("plans");
  const locale = useLocale();
  const now = useNow();
  const roster = useRoster();
  const viewer = useViewer();
  const at = planTime(p);
  const nameOf = (id: string) =>
    id === viewer.personId ? t("you") : (roster.data?.find((r) => r.personId === id)?.displayName ?? id);
  const maps = mapsUrl(p);
  const cancelled = p.status === "cancelled";

  const addToCalendar = () => {
    const ics = planToIcs(p);
    if (!ics) return;
    const url = URL.createObjectURL(new Blob([ics], { type: "text/calendar;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${p.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "plan"}.ics`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const share = async () => {
    const text = [p.title, at !== null ? `${formatLongDate(at, locale)} ${formatTime(at, locale)}` : null, p.venue.name ?? p.venue.text]
      .filter(Boolean)
      .join(" · ");
    const url = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title: p.title, text, url });
        return;
      }
      await navigator.clipboard.writeText(`${text}\n${url}`);
      toast.success(t("copied"));
    } catch {
      // A dismissed share sheet is not an error.
    }
  };

  const groups: Array<{ key: Rsvp["response"]; icon: typeof Check; list: Rsvp[] }> = [
    { key: "yes", icon: Check, list: p.rsvps.filter((r) => r.response === "yes") },
    { key: "maybe", icon: CircleHelp, list: p.rsvps.filter((r) => r.response === "maybe") },
    { key: "no", icon: X, list: p.rsvps.filter((r) => r.response === "no") },
  ];

  return (
    <PageFrame width="narrow">
      <PageHeader
        title={p.title}
        back={{ href: "/plans", label: t("back") }}
        eyebrow={
          cancelled ? (
            <Badge tone="danger" icon={<Ban aria-hidden="true" />}>
              {t("status.cancelled")}
            </Badge>
          ) : (
            <Badge tone={p.status === "confirmed" ? "success" : "neutral"} icon={p.status === "confirmed" ? <Check aria-hidden="true" /> : undefined}>
              {t(`status.${p.status}`)}
            </Badge>
          )
        }
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <Clock className="size-4 text-ink-muted" aria-hidden="true" />
            {at !== null ? (
              <>
                <span className="text-ink">
                  {formatLongDate(at, locale)} · <span className="font-mono tnum">{formatTime(at, locale)}</span>
                </span>
                {now && !cancelled ? <span className="text-ink-muted">({formatRelative(at, locale, now)})</span> : null}
              </>
            ) : (
              <span>{t("noDateLong")}</span>
            )}
          </span>
        }
      />

      <div className="mb-6 flex flex-wrap gap-2">
        {at !== null && !cancelled ? (
          <Button variant="primary" onClick={addToCalendar}>
            <CalendarPlus aria-hidden="true" />
            {t("addToCalendar")}
          </Button>
        ) : null}
        {maps ? (
          <a href={maps} target="_blank" rel="noreferrer" className={buttonClass("outline")}>
            <MapPin aria-hidden="true" />
            {t("openInMaps")}
            <ExternalLink className="!size-3.5 text-ink-muted" aria-hidden="true" />
          </a>
        ) : null}
        <Button variant="quiet" onClick={share}>
          <Share2 aria-hidden="true" />
          {t("share")}
        </Button>
      </div>

      <div className="stagger grid gap-4">
        <Panel aria-labelledby="venue-title">
          <PanelHeader id="venue-title" title={t("venue")} />
          <PanelBody className="grid gap-2">
            {p.venue.name || p.venue.text ? (
              <>
                <p className="text-[1rem] font-semibold text-ink">{p.venue.name ?? p.venue.text}</p>
                {p.venue.address ? <p className="text-body text-ink-soft">{p.venue.address}</p> : null}
                {p.venue.name && p.venue.text && p.venue.text !== p.venue.name ? (
                  <p className="text-meta text-ink-muted">{p.venue.text}</p>
                ) : null}
                {p.venue.rating !== null || p.venue.price_level ? (
                  <p className="flex items-center gap-3 text-meta text-ink-soft">
                    {p.venue.rating !== null ? (
                      <span className="inline-flex items-center gap-1">
                        <Star className="size-3.5 text-ink-muted" aria-hidden="true" />
                        <span className="font-mono tnum">{t("rating", { rating: p.venue.rating.toFixed(1) })}</span>
                      </span>
                    ) : null}
                    {p.venue.price_level ? <span className="font-mono">{p.venue.price_level}</span> : null}
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-body text-ink-soft">{t("noVenue")}</p>
            )}
          </PanelBody>
        </Panel>

        <Panel aria-labelledby="who-title">
          <PanelHeader
            id="who-title"
            title={t("whosComing")}
            description={t("answers", { yes: groups[0].list.length, maybe: groups[1].list.length })}
          />
          {p.rsvps.length === 0 ? (
            <PanelBody>
              <p className="text-body text-ink-soft">{t("noRsvps")}</p>
            </PanelBody>
          ) : (
            <div className="grid gap-4 px-4 pb-5 sm:px-5">
              {groups
                .filter((g) => g.list.length)
                .map((g) => (
                  <section key={g.key} aria-label={t(`rsvp.${g.key}`)}>
                    <h3 className="mb-2 flex items-center gap-1.5 text-label font-medium text-ink-muted">
                      <g.icon className="size-3.5" aria-hidden="true" />
                      {t(`rsvp.${g.key}`)} · {g.list.length}
                    </h3>
                    <ul className="flex flex-wrap gap-2">
                      {g.list.map((r) => (
                        <li key={r.person_id}>
                          <Link
                            href={`/people/${r.person_id}`}
                            className="flex h-10 items-center gap-2 rounded-full border border-hairline bg-studio py-1 pr-3.5 pl-1 text-meta font-medium text-ink transition-colors hover:border-seam"
                          >
                            <PersonAvatar id={r.person_id} name={nameOf(r.person_id)} size={30} />
                            {nameOf(r.person_id)}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
            </div>
          )}
          <p className="border-t border-hairline px-4 py-3 text-label text-ink-muted sm:px-5">{t("answerInChat")}</p>
        </Panel>

        <p className="px-1 text-label text-ink-muted">
          {t("plannedBy", { name: nameOf(p.created_by), date: formatDate(Date.parse(p.created_at), locale) })}
        </p>
      </div>
    </PageFrame>
  );
}
