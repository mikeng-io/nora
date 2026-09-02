"use client";

import { useMemo, useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { Car, BatteryLow, Home } from "lucide-react";
import {
  fetchPresence,
  fetchPresenceHistory,
  fetchPresencePlaces,
  type PresenceEntry,
  type PresenceHistoryPoint,
  type PresencePlace,
} from "@/lib/presence-schema";
import { hueOf } from "@/lib/person-color";
import { Card } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { PersonAvatar } from "@/components/person-avatar";
import { cn } from "@/lib/utils";
import { PresenceAnalytics, SurfaceTabs, type MapSurface } from "@/components/presence-analytics";

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
    fetchPresenceHistory(selected, hours)
      .then((h) => {
        if (!cancelled) setHistory(h.points);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    return () => {
      cancelled = true;
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

      <SurfaceTabs active={surface} onChange={setSurface} />

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
      {!error && !entries && <p className="text-text-muted">{t("loading")}</p>}

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
}: {
  entry: PresenceEntry;
  selected: boolean;
  onClick: () => void;
}) {
  const t = useTranslations("map");
  const hue = hueOf(entry.personId);
  const fix = entry.current;

  return (
    <button
      onClick={onClick}
      className={cn(
        "group flex w-full items-center gap-2.5 rounded-lg border-l-[3px] border-y border-r p-2.5 text-left transition-all hover:-translate-y-px hover:shadow-sm active:translate-y-0",
        selected
          ? "border-y-accent/40 border-r-accent/40 bg-accent/10"
          : "border-y-border border-r-border bg-bg-surface hover:bg-bg-elevated",
      )}
      style={{ borderLeftColor: fix ? `hsl(${hue} 65% 50%)` : "var(--border-strong)" }}
    >
      <PersonAvatar
        personId={entry.personId}
        name={entry.displayName}
        size="md"
        muted={!fix}
        className="transition-transform group-hover:scale-105"
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{entry.displayName}</div>
        <div className="flex items-center gap-1.5 truncate font-mono text-[10px] uppercase tracking-wide text-text-muted">
          {fix ? t(`state.${fix.wireState}`) : t("noFix")}
          {fix?.isDriving && <Car className="h-3 w-3 shrink-0" />}
          {fix?.batteryPercent != null && fix.batteryPercent < 20 && (
            <BatteryLow className="h-3 w-3 shrink-0 text-danger" />
          )}
          {fix && <span className="normal-case">· {relativeAge(fix.ageSeconds)}</span>}
        </div>
      </div>
    </button>
  );
}

function relativeAge(ageSeconds: number): string {
  if (ageSeconds < 60) return "now";
  const min = Math.round(ageSeconds / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  return `${Math.round(hr / 24)}d`;
}
