"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, LogIn, LogOut, Skull, Sparkles } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServerActivity, type ActivityEvent } from "@/lib/games-schema";
import { formatDateTime } from "@/lib/games-format";
import { Card, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { Pagination } from "@/components/ui/pagination";

const PAGE_SIZE = 50;

const EVENT_ICON: Record<ActivityEvent["type"], typeof LogIn> = {
  "player.joined": LogIn,
  "player.left": LogOut,
  "player.died": Skull,
  "world.event": Sparkles,
};

export default function ActivityPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("games");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    fetchServerActivity(id, { page, pageSize: PAGE_SIZE })
      .then((res) => {
        if (cancelled) return;
        setEvents(res.data);
        setHasMore(res.pagination.hasMore);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, page]);

  return (
    <div>
      <Link
        href={`/games/${id}`}
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      <h1 className="mb-6 text-[22px] font-medium tracking-tight">{t("activityHeading")}</h1>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !events && <p className="text-text-muted">{t("loading")}</p>}
      {!error && events && events.length === 0 && (
        <p className="text-text-muted">{t("noActivity")}</p>
      )}

      {events && events.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-border">
              {events.map((event) => (
                <ActivityRow key={event.id} event={event} locale={locale} />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {events && (events.length > 0 || page > 1) && (
        <Pagination
          page={page}
          hasMore={hasMore}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => p + 1)}
          pageLabel={(p) => t("pageLabel", { page: p })}
        />
      )}
    </div>
  );
}

function ActivityRow({ event, locale }: { event: ActivityEvent; locale: string }) {
  const t = useTranslations("games");
  const Icon = EVENT_ICON[event.type];
  const isZhHant = locale.startsWith("zh");

  let message: string;
  switch (event.type) {
    case "player.joined":
      message = t("eventJoined", { name: event.playerName ?? "" });
      break;
    case "player.left":
      message = t("eventLeft", { name: event.playerName ?? "" });
      break;
    case "player.died": {
      const cause = (isZhHant ? event.deathCauseZhHant : event.deathCause) ?? event.deathCause ?? "";
      message = t("eventDied", { name: event.playerName ?? "", cause });
      break;
    }
    case "world.event":
      message = (isZhHant ? event.messageZhHant : event.message) ?? event.message ?? "";
      break;
  }

  return (
    <li className="flex items-start gap-3 p-3.5">
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-muted" />
      <p className="flex-1 text-[13px] text-text-secondary">{message}</p>
      <span className="shrink-0 whitespace-nowrap font-mono text-[10px] text-text-muted">
        {formatDateTime(event.occurredAt, locale)}
      </span>
    </li>
  );
}
