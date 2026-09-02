"use client";

import { useMemo, useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { Car, BatteryLow, ChevronRight, Home } from "lucide-react";
import {
  fetchPresence,
  fetchPresenceHistory,
  fetchPresencePlaces,
  STALE_DRIVING_SECONDS,
  type PresenceEntry,
  type PresenceHistoryPoint,
  type PresencePlace,
} from "@/lib/presence-schema";
import { relativeAge } from "@/lib/presence-format";
import { hueOf } from "@/lib/person-color";
import { Card } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { cn } from "@/lib/utils";
import { PresenceAnalytics, SurfaceTabs, type MapSurface } from "@/components/presence-analytics";
import { PresenceProfile } from "@/components/presence-profile";

// Leaflet touches `window` at import time — must never run during SSR.
const PresenceMap = dynamic(
  () => import("@/components/presence-map").then((m) => m.PresenceMap),
  {
    ssr: false,
    loading: () => <div className="h-full w-full bg-bg-elevated" />,
  },
);

const POLL_INTERVAL_MS = 20_000;
const HOUR_OPTIONS = [1, 6, 24, 168] as const;

export default function MapPage() {
  const t = useTranslations("map");
  const [entries, setEntries] = useState<PresenceEntry[] | null>(null);
  const [places, setPlaces] = useState<PresencePlace[]>([]);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [hours, setHours] = useState<number>(24);
  const [history, setHistory] = useState<PresenceHistoryPoint[]>([]);
  const [focusPlace, setFocusPlace] = useState<[number, number] | null>(null);
  const [surface, setSurface] = useState<MapSurface>("live");
  const [profileId, setProfileId] = useState<string | null>(null);

  const profileEntry = useMemo(
    () => entries?.find((entry) => entry.personId === profileId) ?? null,
    [entries, profileId],
  );

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchPresence()
        .then((e) => {
          if (!cancelled) {
            setEntries(e);
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

  useEffect(() => {
    fetchPresencePlaces()
      .then(setPlaces)
      .catch(() => setPlaces([]));
  }, []);

  useEffect(() => {
    if (!selected) {
      setHistory([]);
      return;
    }
    let cancelled = false;
    // Same cadence as the position poll above — otherwise the trail line
    // only draws once, at selection time, and never extends while the dot
    // keeps moving on every 20s position update.
    const tick = () => {
      fetchPresenceHistory(selected, hours)
        .then((h) => {
          if (!cancelled) setHistory(h.points);
        })
        .catch(() => {
          if (!cancelled) setHistory([]);
        });
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [selected, hours]);

  const { live, others } = useMemo(() => {
    if (!entries) return { live: [], others: [] };
    const withFix = entries
      .filter((e) => e.current)
      .sort((a, b) => a.current!.ageSeconds - b.current!.ageSeconds);
    const withoutFix = entries.filter((e) => !e.current);
    return { live: withFix, others: withoutFix };
  }, [entries]);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-6 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      <SurfaceTabs
        active={surface}
        onChange={(next) => {
          setSurface(next);
          // The drawer belongs to the live surface — leaving it open across
          // a tab switch would resurrect it, stale, on the way back.
          setProfileId(null);
        }}
      />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
      {!error && !entries && <p className="text-text-muted">{t("loading")}</p>}

      {surface === "live" && profileEntry && (
        <PresenceProfile entry={profileEntry} onClose={() => setProfileId(null)} />
      )}

      {surface !== "live" ? (
        <PresenceAnalytics
          surface={surface}
          entries={entries}
          selectedSubjectId={selected}
          onSubjectSelect={setSelected}
        />
      ) : entries ? (
        <div className="grid gap-4 md:grid-cols-[1fr_290px]">
          <div>
            {selected && (
              <div className="mb-3 flex items-center gap-1.5">
                <span className="mr-1 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                  {t("lookback")}
                </span>
                {HOUR_OPTIONS.map((h) => (
                  <button
                    key={h}
                    onClick={() => setHours(h)}
                    className={cn(
                      "rounded-full border px-3 py-1 text-[11.5px] transition-colors",
                      hours === h
                        ? "border-accent/40 bg-accent/10 text-accent"
                        : "border-border text-text-secondary hover:bg-bg-elevated",
                    )}
                  >
                    {t(`hours.${h}`)}
                  </button>
                ))}
              </div>
            )}
            <Card className="h-[50vh] overflow-hidden md:h-[65vh]">
              <PresenceMap
                entries={entries}
                places={places}
                selectedPersonId={selected}
                onSelect={(id) => setSelected((cur) => (cur === id ? null : id))}
                onOpenProfile={(id) => setProfileId(id)}
                historyPoints={history}
                focusPlace={focusPlace}
              />
            </Card>
          </div>

          <div className="space-y-4">
            <div className="space-y-1.5">
              {live.length > 0 && (
                <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  {t("liveSection")} · {live.length}
                </p>
              )}
              <div className="space-y-1.5">
                {live.map((entry) => (
                  <PersonRow
                    key={entry.personId}
                    entry={entry}
                    selected={selected === entry.personId}
                    onClick={() => setSelected((cur) => (cur === entry.personId ? null : entry.personId))}
                    onOpenProfile={() => setProfileId(entry.personId)}
                  />
                ))}
              </div>

              {others.length > 0 && (
                <>
                  <p className="mb-1.5 mt-3 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                    {t("otherSection")} · {others.length}
                  </p>
                  <div className="space-y-1.5">
                    {others.map((entry) => (
                      <PersonRow
                        key={entry.personId}
                        entry={entry}
                        selected={selected === entry.personId}
                        onClick={() => setSelected((cur) => (cur === entry.personId ? null : entry.personId))}
                        onOpenProfile={() => setProfileId(entry.personId)}
                      />
                    ))}
                  </div>
                </>
              )}
            </div>

            {places.length > 0 && (
              <div>
                <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
                  {t("placesSection")} · {places.length}
                </p>
                <div className="space-y-1 rounded-lg border border-border bg-bg-surface p-1">
                  {places.map((place) => (
                    <button
                      key={place.name}
                      onClick={() => setFocusPlace([place.lat, place.lng])}
                      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] text-text-secondary transition-colors hover:bg-bg-elevated hover:text-text-primary"
                    >
                      <Home className="h-3.5 w-3.5 shrink-0 text-text-muted" />
                      <span className="truncate">{place.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PersonRow({
  entry,
  selected,
  onClick,
  onOpenProfile,
}: {
  entry: PresenceEntry;
  selected: boolean;
  onClick: () => void;
  onOpenProfile: () => void;
}) {
  const t = useTranslations("map");
  const hue = hueOf(entry.personId);
  const fix = entry.current;

  // The row is a div containing TWO buttons, not one button with a nested
  // control (invalid HTML, and screen readers mangle it): the body selects/
  // follows, the chevron opens the profile drawer — the same two actions a
  // map marker exposes (select on click, Profile inside its popup).
  return (
    <div
      className={cn(
        "group flex w-full items-center rounded-lg border-l-[3px] border-y border-r transition-all hover:-translate-y-px hover:shadow-sm active:translate-y-0",
        selected
          ? "border-y-accent/40 border-r-accent/40 bg-accent/10"
          : "border-y-border border-r-border bg-bg-surface hover:bg-bg-elevated",
      )}
      style={{ borderLeftColor: fix ? `hsl(${hue} 65% 50%)` : "var(--border-strong)" }}
    >
      <button
        type="button"
        onClick={onClick}
        aria-pressed={selected}
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-l-lg p-2.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span
          className="inline-flex shrink-0 rounded-full ring-2 transition-transform group-hover:scale-105"
          style={{
            // @ts-expect-error -- CSS custom value, not a token
            "--tw-ring-color": `hsl(${hue} 60% 55% / ${fix ? 0.35 : 0.12})`,
          }}
        >
          <PersonAvatar seed={entry.personId} size={32} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{entry.displayName}</div>
          <div className="flex items-center gap-1.5 truncate font-mono text-[10px] uppercase tracking-wide text-text-muted">
            {fix ? t(`state.${fix.wireState}`) : t("noFix")}
            {fix?.isDriving && (
              <span
                className={cn(
                  "flex items-center",
                  fix.ageSeconds > STALE_DRIVING_SECONDS && "text-text-muted/60",
                )}
                title={fix.ageSeconds > STALE_DRIVING_SECONDS ? t("drivingStale") : t("driving")}
              >
                <Car className="h-3 w-3 shrink-0" />
                <span className="sr-only">
                  {fix.ageSeconds > STALE_DRIVING_SECONDS ? t("drivingStale") : t("driving")}
                </span>
              </span>
            )}
            {fix?.batteryPercent != null && fix.batteryPercent < 20 && (
              <span title={t("batteryLow")}>
                <BatteryLow className="h-3 w-3 shrink-0 text-danger" />
                <span className="sr-only">{t("batteryLow")}</span>
              </span>
            )}
            {fix && <span className="normal-case">· {relativeAge(fix.ageSeconds)}</span>}
          </div>
        </div>
      </button>
      <button
        type="button"
        onClick={onOpenProfile}
        aria-label={`${t("profile.open")} — ${entry.displayName}`}
        title={t("profile.open")}
        className="relative mr-1.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-muted transition-colors before:absolute before:-inset-1.5 before:content-[''] hover:bg-bg-elevated hover:text-text-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

