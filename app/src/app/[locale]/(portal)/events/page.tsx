"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/routing";
import { fetchEvents, type Event } from "@/lib/events-schema";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { formatWhen, STATUS_BADGE_VARIANT } from "@/lib/events-format";

// Participant-scoped, not conversation-scoped: `fetchEvents()` (via the
// session-resolved personId the proxy route supplies) already returns
// exactly "events I created or RSVP'd to" — nothing left for this page to
// pick or filter by, so there's no group switcher to build.
export default function EventsPage() {
  const t = useTranslations("events");
  const locale = useLocale();
  const [events, setEvents] = useState<Event[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchEvents()
      .then((e) => {
        if (cancelled) return;
        setEvents(e);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
      {!error && !events && <p className="text-text-muted">{t("loading")}</p>}
      {!error && events && events.length === 0 && (
        <p className="text-text-muted">{t("empty")}</p>
      )}

      {events && events.length > 0 && (
        <div className="grid grid-cols-1 gap-3">
          {events.map((ev) => (
            <EventCard key={ev.id} event={ev} locale={locale} />
          ))}
        </div>
      )}
    </div>
  );
}

function EventCard({ event, locale }: { event: Event; locale: string }) {
  const t = useTranslations("events");

  return (
    <Link href={`/events/${event.id}`} className="block">
      <Card className="transition-colors hover:bg-bg-elevated">
        <CardHeader className="flex-row items-center justify-between space-y-0 p-4 pb-2">
          <CardTitle>{event.title}</CardTitle>
          <Badge variant={STATUS_BADGE_VARIANT[event.status]}>{t(event.status)}</Badge>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <p className="text-[13px] text-text-secondary">
            {event.venue.name ?? event.venue.text ?? t("noVenue")}
          </p>
          {event.when && (
            <p className="mt-1 font-mono text-[11px] text-text-muted">
              {formatWhen(event.when, locale)}
            </p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
