"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Bus, Cloud, BookOpen, Puzzle, Image as ImageIcon, Hash } from "lucide-react";
import { fetchStatus, type Status } from "@/lib/status-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { StatusDot } from "@/components/ui/status-dot";
import { CapabilityTile } from "@/components/capability-tile";

const POLL_INTERVAL_MS = 20_000;

// Coming-soon capability tiles (design spec: ship the placeholder now, the
// backend later — every idea here traces to a real, already-built domain
// concept; see nora/domain/{language_scoring,models}.py. "designed" = real
// data already exists somewhere, just not surfaced over web yet. "future" =
// would need genuinely new backend work.
const CAPABILITIES = [
  { id: "roomVocabulary", icon: Hash, variant: "designed" as const },
  { id: "weather", icon: Cloud, variant: "designed" as const },
  { id: "transit", icon: Bus, variant: "designed" as const },
  { id: "doctrine", icon: BookOpen, variant: "future" as const },
  { id: "skills", icon: Puzzle, variant: "designed" as const },
  { id: "mediaGallery", icon: ImageIcon, variant: "future" as const },
];

export default function StatusPage() {
  const t = useTranslations("status");
  const locale = useLocale();
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

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
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
                <div className="flex items-center gap-2">
                  <StatusDot
                    reachable={status.store.reachable}
                    title={status.store.reachable ? t("storeOk") : t("storeDown")}
                  />
                </div>
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
                <div className="flex items-center gap-2">
                  <StatusDot
                    reachable={status.honcho.reachable}
                    title={status.honcho.reachable ? t("memoryOk") : t("memoryDown")}
                  />
                </div>
                <p className="mt-1.5 text-[13px] text-text-secondary">
                  {status.honcho.reachable ? t("memoryOk") : t("memoryDown")}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card className="mb-8">
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
                      <span
                        className="text-text-secondary"
                        title={new Date(r.due_at).toLocaleString(locale, {
                          dateStyle: "medium",
                          timeStyle: "short",
                        })}
                      >
                        {formatRelative(r.due_at, locale)}
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

          <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
            {t("capabilitiesHeading")}
          </p>
          <p className="mb-4 max-w-[56ch] text-[13px] text-text-secondary">{t("capabilitiesDek")}</p>
          <div className="grid grid-cols-2 gap-3">
            {CAPABILITIES.map((c) => (
              <CapabilityTile
                key={c.id}
                icon={c.icon}
                title={t(`capability.${c.id}.title`)}
                description={t(`capability.${c.id}.description`)}
                variant={c.variant}
                badgeLabel={t(c.variant === "designed" ? "badgePlanned" : "badgeIdea")}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function formatRelative(iso: string, locale: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();
  const diffMin = Math.round(diffMs / 60_000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(diffMin) < 60) return rtf.format(diffMin, "minute");
  const diffHour = Math.round(diffMin / 60);
  if (Math.abs(diffHour) < 24) return rtf.format(diffHour, "hour");
  const diffDay = Math.round(diffHour / 24);
  return rtf.format(diffDay, "day");
}
