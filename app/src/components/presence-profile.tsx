"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Car, Route, X } from "lucide-react";
import {
  fetchPresenceAnalyticsPlaces,
  fetchPresenceRhythm,
  fetchPresenceRoutes,
  type PresenceAnalyticsPlace,
  type PresenceEntry,
  type PresenceRhythm,
  type PresenceRoutes,
} from "@/lib/presence-schema";
import {
  corridorEndpoints,
  formatDate,
  formatDuration,
  relativeAge,
} from "@/lib/presence-format";
import { hueOf } from "@/lib/person-color";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { Alert } from "@/components/ui/alert";

// The digest reads the same 30-day window the analytics tabs default to — a
// profile that quietly used a different window would disagree with the tabs
// the operator cross-checks it against.
const PROFILE_WINDOW_DAYS = 30;
const TOP_PLACES = 3;
const TOP_CORRIDORS = 5;
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

// A per-person digest as a slide-over drawer on the live map (the Life360
// "tap a person" pattern), NOT another surface tab: corridors/places/rhythm
// belong to someone, and the person row is the entry point that says so.
// Every section is a re-read of the existing analytics endpoints — no new
// derivation lives here.
export function PresenceProfile({
  entry,
  onClose,
}: {
  entry: PresenceEntry;
  onClose: () => void;
}) {
  const t = useTranslations("map");
  const hue = hueOf(entry.personId);
  const fix = entry.current;
  const [places, setPlaces] = useState<PresenceAnalyticsPlace[] | null>(null);
  const [rhythm, setRhythm] = useState<PresenceRhythm | null>(null);
  const [routes, setRoutes] = useState<PresenceRoutes | null>(null);
  const [failed, setFailed] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    setPlaces(null);
    setRhythm(null);
    setRoutes(null);
    setFailed(false);
    const subjectId = entry.personId;
    // allSettled, not all: one endpoint down must not blank the other two
    // sections — each falls back to its own empty state, and only a TOTAL
    // failure shows the error (the same fail-closed split the surfaces use).
    Promise.allSettled([
      fetchPresenceAnalyticsPlaces().then(
        (data) => data.subjects.find((s) => s.subjectId === subjectId)?.places ?? [],
      ),
      fetchPresenceRhythm(subjectId, PROFILE_WINDOW_DAYS),
      fetchPresenceRoutes(subjectId, PROFILE_WINDOW_DAYS),
    ]).then(([placesResult, rhythmResult, routesResult]) => {
      if (cancelled) return;
      if (placesResult.status === "fulfilled") setPlaces(placesResult.value);
      if (rhythmResult.status === "fulfilled") setRhythm(rhythmResult.value);
      if (routesResult.status === "fulfilled") setRoutes(routesResult.value);
      if (
        placesResult.status === "rejected" &&
        rhythmResult.status === "rejected" &&
        routesResult.status === "rejected"
      ) {
        setFailed(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [entry.personId]);

  // Focus lands inside the dialog on open so keyboard users don't tab
  // through the page behind it; Escape closes, same as the backdrop.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const topPlaces = useMemo(
    () => [...(places ?? [])].sort((a, b) => b.totalDwellMs - a.totalDwellMs).slice(0, TOP_PLACES),
    [places],
  );
  const placeDwellTotal = useMemo(
    () => (places ?? []).reduce((sum, place) => sum + place.totalDwellMs, 0),
    [places],
  );

  const dwellPerWeekday = useMemo(() => {
    const totals = Array.from({ length: 7 }, () => 0);
    for (const bin of rhythm?.bins ?? []) {
      totals[Math.floor(bin.hourOfWeek / 24)] += bin.totalDwellMs;
    }
    return totals;
  }, [rhythm]);
  const peakBin = useMemo(
    () =>
      (rhythm?.bins ?? []).reduce<PresenceRhythm["bins"][number] | null>(
        (best, bin) => (best === null || bin.totalDwellMs > best.totalDwellMs ? bin : best),
        null,
      ),
    [rhythm],
  );

  const topCorridors = useMemo(
    () =>
      [...(routes?.corridors ?? [])]
        .sort((a, b) => b.sampleCount - a.sampleCount)
        .slice(0, TOP_CORRIDORS),
    [routes],
  );
  const unmatchedCount = useMemo(
    () => (routes?.trips ?? []).filter((trip) => trip.corridorId === null).length,
    [routes],
  );

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={entry.displayName}>
      <button
        type="button"
        aria-label={t("profile.close")}
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-black/50"
      />
      <aside className="animate-drawer-in absolute inset-y-0 right-0 flex w-full max-w-sm flex-col border-l border-border bg-bg-surface shadow-2xl">
        <div className="flex items-center gap-3 border-b border-border px-4 py-3.5">
          <span
            className="inline-flex shrink-0 rounded-full ring-2"
            style={{
              // @ts-expect-error -- CSS custom value, not a token
              "--tw-ring-color": `hsl(${hue} 60% 55% / 0.35)`,
            }}
          >
            <PersonAvatar seed={entry.personId} size={40} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-medium">{entry.displayName}</div>
            <div className="flex items-center gap-1.5 truncate font-mono text-[10px] uppercase tracking-wide text-text-muted">
              {fix ? t(`state.${fix.wireState}`) : t("noFix")}
              {fix?.isDriving && (
                <span title={t("driving")}>
                  <Car className="h-3 w-3 shrink-0" />
                  <span className="sr-only">{t("driving")}</span>
                </span>
              )}
              {fix && <span className="normal-case">· {relativeAge(fix.ageSeconds)}</span>}
            </div>
            {fix?.address && (
              <div className="mt-0.5 truncate text-[11.5px] text-text-secondary">{fix.address}</div>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label={t("profile.close")}
            className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-muted transition-colors before:absolute before:-inset-1.5 before:content-[''] hover:bg-bg-elevated hover:text-text-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {failed ? (
            <div className="p-4">
              <Alert variant="destructive">{t("analytics.error")}</Alert>
            </div>
          ) : (
            <>
              <Section title={t("analytics.tabs.places")}>
                {places === null ? (
                  <SectionPending />
                ) : topPlaces.length === 0 ? (
                  <SectionEmpty label={t("analytics.empty.noPlaces")} />
                ) : (
                  <div className="mt-2.5 space-y-2.5">
                    {topPlaces.map((place, index) => {
                      const share = placeDwellTotal > 0 ? place.totalDwellMs / placeDwellTotal : 0;
                      return (
                        <div key={`${place.kind}-${place.lat}-${index}`}>
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="truncate text-[12.5px] font-medium">
                              {place.label ?? t("analytics.unnamed")}
                            </span>
                            <span className="shrink-0 font-mono text-[10px] text-text-muted">
                              {formatDuration(place.totalDwellMs)} · {Math.round(share * 100)}%
                            </span>
                          </div>
                          <div className="mt-1 h-1 overflow-hidden rounded-full bg-bg-elevated">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${Math.round(share * 100)}%`,
                                backgroundColor: `hsl(${hue} 68% 50% / 0.65)`,
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Section>

              <Section title={t("analytics.tabs.rhythm")}>
                {rhythm === null ? (
                  <SectionPending />
                ) : rhythm.bins.length === 0 ? (
                  <SectionEmpty label={t("analytics.empty.noRhythm")} />
                ) : (
                  <div className="mt-2.5">
                    <div className="grid grid-cols-7 items-end gap-1">
                      {dwellPerWeekday.map((dwell, day) => {
                        const maxDwell = Math.max(...dwellPerWeekday, 1);
                        return (
                          <div key={day} className="flex flex-col items-center gap-1">
                            <div className="flex h-12 w-full items-end rounded-[3px] bg-bg-elevated">
                              <div
                                className="w-full rounded-[3px]"
                                style={{
                                  height: dwell === 0 ? "0%" : `${Math.max(8, (dwell / maxDwell) * 100)}%`,
                                  backgroundColor: `hsl(${hue} 68% 50% / 0.65)`,
                                }}
                              />
                            </div>
                            <span className="font-mono text-[9px] uppercase text-text-muted">
                              {t(`analytics.days.${WEEKDAYS[day]}`)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    {peakBin && (
                      <div className="mt-2 font-mono text-[10px] text-text-muted">
                        {t("profile.peak")} ·{" "}
                        {t(`analytics.days.${WEEKDAYS[Math.floor(peakBin.hourOfWeek / 24)]}`)}{" "}
                        {String(peakBin.hourOfWeek % 24).padStart(2, "0")}:00
                      </div>
                    )}
                  </div>
                )}
              </Section>

              <Section title={t("analytics.tabs.routes")}>
                {routes === null ? (
                  <SectionPending />
                ) : routes.trips.length === 0 ? (
                  <SectionEmpty label={t("analytics.empty.noRoutes")} />
                ) : (
                  <div className="mt-2.5 space-y-1.5">
                    {topCorridors.map((corridor) => {
                      const ends = corridorEndpoints(corridor);
                      return (
                        <div
                          key={corridor.id}
                          className="flex items-center gap-2.5 rounded-md border border-border px-2.5 py-2"
                        >
                          <Route className="h-3.5 w-3.5 shrink-0 text-accent" />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[12px] font-medium">
                              {ends ? `${ends.from} → ${ends.to}` : `C${corridor.id}`}
                            </div>
                            <div className="font-mono text-[9px] uppercase tracking-wider text-text-muted">
                              {t("profile.trips", { count: corridor.sampleCount })} ·{" "}
                              {t("profile.lastSeen", { date: formatDate(corridor.lastSeenTs) })}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                    {unmatchedCount > 0 && (
                      <div className="px-1 pt-0.5 font-mono text-[9px] uppercase tracking-wider text-text-muted">
                        {t("profile.unmatched", { count: unmatchedCount })}
                      </div>
                    )}
                  </div>
                )}
              </Section>

              <div className="px-4 py-3 font-mono text-[9px] uppercase tracking-wider text-text-muted">
                {t("profile.window", { days: PROFILE_WINDOW_DAYS })}
              </div>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-border px-4 py-4">
      <h3 className="font-mono text-[10px] uppercase tracking-wider text-text-muted">{title}</h3>
      {children}
    </section>
  );
}

function SectionPending() {
  const t = useTranslations("map");
  return <p className="mt-2 font-mono text-[11px] text-text-muted">{t("analytics.loading")}</p>;
}

function SectionEmpty({ label }: { label: string }) {
  return <p className="mt-2 font-mono text-[11px] text-text-muted">{label}</p>;
}
