"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchEvent, type EventDetail } from "@/lib/events-schema";
import { formatWhen, STATUS_BADGE_VARIANT } from "@/lib/events-format";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { Skeleton } from "@/components/ui/skeleton";

const RSVP_BADGE_VARIANT: Record<EventDetail["rsvps"][number]["response"], "live" | "designed" | "danger"> = {
  yes: "live",
  no: "danger",
  maybe: "designed",
};

export default function EventDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("events");
  const locale = useLocale();
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchEvent(id)
      .then((e) => {
        if (cancelled) return;
        setEvent(e);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div>
      <Link
        href="/events"
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !event && <EventDetailSkeleton />}

      {event && (
        <>
          <div className="mb-6 flex items-center gap-2.5">
            <h1 className="text-[26px] font-medium tracking-tight">{event.title}</h1>
            <Badge variant={STATUS_BADGE_VARIANT[event.status]}>{t(event.status)}</Badge>
          </div>

          {event.when && (
            <p className="mb-6 font-mono text-[11px] text-text-muted">{formatWhen(event.when, locale)}</p>
          )}

          <Card className="mb-6">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("venueLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-1.5 p-4 pt-0">
              <p className="text-[13px] text-text-primary">
                {event.venue.name ?? event.venue.text ?? t("noVenue")}
              </p>
              {event.venue.address && (
                <p className="text-[12.5px] text-text-secondary">{event.venue.address}</p>
              )}
              {event.venue.rating != null && (
                <p className="text-[12.5px] text-text-secondary">
                  {t("rating", { rating: event.venue.rating })}
                </p>
              )}
              {event.venue.maps_url && (
                <a
                  href={event.venue.maps_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[12.5px] text-accent underline underline-offset-2"
                >
                  {t("openInMaps")}
                </a>
              )}
            </CardContent>
          </Card>

          <Card className="mb-6">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("rsvpsLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              {event.rsvps.length === 0 ? (
                <p className="font-mono text-[12.5px] text-text-muted">{t("noRsvps")}</p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {event.rsvps.map((r) => (
                    <li key={r.person_id} className="flex items-center gap-2.5">
                      <PersonAvatar seed={r.person_id} size={24} className="rounded-full overflow-hidden" />
                      <span className="flex-1 truncate text-[13px] text-text-secondary">{r.person_id}</span>
                      <Badge variant={RSVP_BADGE_VARIANT[r.response]}>{t(r.response)}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function EventDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2.5">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="h-32" />
      <Skeleton className="h-24" />
    </div>
  );
}
