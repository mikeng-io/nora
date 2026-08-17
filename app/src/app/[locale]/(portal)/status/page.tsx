"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { fetchStatus, type Status } from "@/lib/status-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

const POLL_INTERVAL_MS = 20_000;

export default function StatusPage() {
  const t = useTranslations("status");
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchStatus()
        .then((s) => {
          if (!cancelled) {
            setStatus(s);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      {error && <Alert variant="destructive" className="mb-6">{t("error")}</Alert>}
      {!error && !status && <p className="text-text-muted">{t("loading")}</p>}

      {status && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3">
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  {t("storeLabel")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <Dot reachable={status.store.reachable} />
                <p className="mt-1.5 text-[13px] text-text-secondary">
                  {status.store.reachable ? t("storeOk") : t("storeDown")}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  {t("memoryLabel")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                <Dot reachable={status.honcho.reachable} />
                <p className="mt-1.5 text-[13px] text-text-secondary">
                  {status.honcho.reachable ? t("memoryOk") : t("memoryDown")}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{t("remindersLabel")}</CardTitle>
            </CardHeader>
            <CardContent>
              {status.reminders.count === 0 ? (
                <p className="font-mono text-[12.5px] text-text-secondary">{t("remindersNone")}</p>
              ) : (
                <ul className="space-y-2">
                  {status.reminders.upcoming.map((r, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between border-b border-border pb-2 text-[13px] last:border-0 last:pb-0"
                    >
                      <span className="text-text-secondary">
                        {new Date(r.due_at).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "numeric",
                          minute: "2-digit",
                        })}
                      </span>
                      <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">
                        {r.platform === "discord"
                          ? t("remindersPlatformDiscord")
                          : t("remindersPlatformSignal")}
                      </span>
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

function Dot({ reachable }: { reachable: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className={`h-1.5 w-1.5 rounded-full ${reachable ? "bg-success" : "bg-danger"}`} />
    </span>
  );
}
