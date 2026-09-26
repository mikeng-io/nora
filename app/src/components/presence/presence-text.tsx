"use client";

import { useLocale, useTranslations } from "next-intl";
import { BatteryLow, Car, CloudOff, EyeOff, MapPinOff, PhoneOff } from "lucide-react";
import type { DerivedPlace, PresenceEntry, SavedPlace } from "@/lib/api/presence";
import { formatAge, formatNumber, mediumAddress, shortAddress } from "@/lib/format";
import { LOW_BATTERY, namePlace, presenceKind, type PresenceKind } from "@/lib/presence";
import { cn } from "@/lib/cn";

export interface PresenceWords {
  kind: PresenceKind;
  /** Where, in words: "At Home", "Nathan Road", "Driving". */
  primary: string;
  /** The address line under it, when the primary is a place name. */
  address: string | null;
  /** "4m", "2h": the fix's age, compact. */
  age: string | null;
  battery: number | null;
  lowBattery: boolean;
  speed: string | null;
}

export function usePresenceWords(
  entry: PresenceEntry,
  saved?: SavedPlace[],
  derived?: DerivedPlace[],
): PresenceWords {
  const t = useTranslations("presence");
  const locale = useLocale();
  const kind = presenceKind(entry);
  const fix = entry.current;

  let primary: string;
  let address: string | null = null;
  if (!fix || kind === "none") primary = t("kind.none");
  else if (kind === "unavailable") primary = t("kind.unavailable");
  else if (kind === "sharing_off" || kind === "disconnected" || kind === "no_fix") primary = t(`kind.${kind}`);
  else if (kind === "driving" || kind === "driving-stale") {
    primary = t("kind.driving");
    address = shortAddress(fix.address);
  } else {
    const named = namePlace(fix, saved, derived);
    const street = mediumAddress(fix.address);
    if (named?.source === "saved") primary = t("atPlace", { place: named.name });
    else if (named?.source === "derived" && named.label) primary = t("atPlace", { place: named.label });
    else if (named?.source === "derived") primary = t(named.kind === "home" ? "atHome" : "atWork");
    else primary = street ?? t("somewhere");
    address = named ? street : null;
  }

  return {
    kind,
    primary,
    address,
    age: fix && kind !== "unavailable" ? formatAge(fix.ageSeconds, locale) : null,
    battery: fix?.batteryPercent ?? null,
    lowBattery: (fix?.batteryPercent ?? 100) <= LOW_BATTERY,
    speed:
      kind === "driving" && fix?.speedKmh !== undefined
        ? t("speed", { speed: formatNumber(fix.speedKmh, locale) })
        : null,
  };
}

const KIND_ICON: Partial<Record<PresenceKind, typeof Car>> = {
  driving: Car,
  "driving-stale": Car,
  sharing_off: EyeOff,
  disconnected: PhoneOff,
  no_fix: MapPinOff,
  none: MapPinOff,
  unavailable: CloudOff,
};

/** The small icon that goes before a state's words (never colour alone). */
export function PresenceIcon({ kind, className }: { kind: PresenceKind; className?: string }) {
  const Icon = KIND_ICON[kind];
  if (!Icon) return null;
  return <Icon aria-hidden="true" className={cn("size-3.5 shrink-0", className)} />;
}

/** "4m · 64% · 58 km/h", with a low battery said in words. */
export function PresenceMeta({ words, className }: { words: PresenceWords; className?: string }) {
  const t = useTranslations("presence");
  const parts: React.ReactNode[] = [];
  if (words.kind === "driving-stale") parts.push(t("kind.driving-stale"));
  else if (words.kind === "stale" && words.age) parts.push(t("seenAgo", { age: words.age }));
  else if (words.age) parts.push(words.age);
  if (words.speed) parts.push(words.speed);
  if (words.battery !== null) {
    parts.push(
      words.lowBattery ? (
        <span key="b" className="inline-flex items-center gap-1 text-caution">
          <BatteryLow className="size-3.5" aria-hidden="true" />
          {t("batteryLow", { percent: words.battery })}
        </span>
      ) : (
        t("battery", { percent: words.battery })
      ),
    );
  }
  if (parts.length === 0) return null;
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-x-1.5 text-meta text-ink-muted tnum", className)}>
      {parts.map((p, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          {i > 0 ? <span aria-hidden="true">·</span> : null}
          {p}
        </span>
      ))}
    </span>
  );
}
