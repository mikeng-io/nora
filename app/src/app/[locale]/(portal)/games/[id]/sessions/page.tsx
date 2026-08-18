"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchServerSessions, type Session } from "@/lib/games-schema";
import { formatDateTime, formatDurationMs } from "@/lib/games-format";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Pagination } from "@/components/ui/pagination";

const PAGE_SIZE = 50;

export default function SessionsPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const t = useTranslations("games");
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSessions(null);
    fetchServerSessions(id, { page, pageSize: PAGE_SIZE })
      .then((res) => {
        if (cancelled) return;
        setSessions(res.data);
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

      <h1 className="mb-6 text-[22px] font-medium tracking-tight">{t("sessionsHeading")}</h1>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !sessions && <p className="text-text-muted">{t("loading")}</p>}
      {!error && sessions && sessions.length === 0 && (
        <p className="text-text-muted">{t("noSessions")}</p>
      )}

      {sessions && sessions.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-border">
              {sessions.map((session) => {
                const open = session.leftAt === null;
                return (
                  <li key={session.id} className="flex items-center gap-3 p-3.5">
                    <Link
                      href={`/games/${id}/players/${encodeURIComponent(session.playerKey)}`}
                      className="flex-1 truncate text-[13px] text-text-primary hover:underline"
                    >
                      {session.playerName}
                    </Link>
                    {open ? (
                      <Badge variant="live">{t("online")}</Badge>
                    ) : (
                      <span className="font-mono text-[11px] text-text-muted">
                        {formatDurationMs(session.durationMs ?? 0, locale)}
                      </span>
                    )}
                    <span className="shrink-0 whitespace-nowrap font-mono text-[10px] text-text-muted">
                      {formatDateTime(session.joinedAt, locale)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      )}

      {sessions && (sessions.length > 0 || page > 1) && (
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
