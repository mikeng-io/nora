"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { WifiOff, Sparkles, Hash } from "lucide-react";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";

function initialsOf(name: string) {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

// Stable per-person accent so the same person reads as the same color
// everywhere in the portal (memory card, and later the presence map).
// Pure display hash — not an identifier, never persisted or sent anywhere.
function hueOf(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
}

export default function MemoryPage() {
  const t = useTranslations("memory");
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchMemory()
      .then(setEntries)
      .catch(() => setError(true));
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      {error && <Alert variant="destructive">{t("error")}</Alert>}
      {!error && !entries && <p className="text-text-muted">{t("loading")}</p>}

      {entries && (
        <div className="space-y-6 pb-10">
          <RoomVocabularyTeaser />

          <div className="space-y-3">
            {entries.map((entry) => {
              const hue = hueOf(entry.person_id);
              return (
                <Card key={entry.person_id}>
                  <CardHeader className="flex-row items-center gap-2.5 space-y-0 pb-2">
                    <Avatar
                      className="h-7 w-7 ring-2"
                      style={{
                        backgroundColor: `hsl(${hue} 70% 92%)`,
                        // @ts-expect-error -- CSS custom value, not a token
                        "--tw-ring-color": `hsl(${hue} 60% 55% / 0.35)`,
                      }}
                    >
                      <AvatarFallback
                        style={{ color: `hsl(${hue} 55% 32%)`, backgroundColor: "transparent" }}
                      >
                        {initialsOf(entry.display_name)}
                      </AvatarFallback>
                    </Avatar>
                    <CardTitle>{entry.display_name}</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-0">
                    {!entry.reachable && (
                      <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-danger">
                        <WifiOff className="h-3.5 w-3.5" />
                        {t("unavailable")}
                      </p>
                    )}
                    {entry.reachable && entry.conclusions.length === 0 && (
                      <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-text-muted">
                        <Sparkles className="h-3.5 w-3.5" />
                        {t("nothingYet")}
                      </p>
                    )}
                    {entry.reachable && entry.conclusions.length > 0 && (
                      <ul className="list-disc space-y-1 pl-4 text-[13.5px] text-text-secondary">
                        {entry.conclusions.map((c, i) => (
                          <li key={i}>{c}</li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// Preview of the room-vocabulary feature (nora/domain's RoomLexeme scoring —
// live/fading/stale terms weighted by frequency + speaker spread). No real
// words are shown yet — that needs a new backend endpoint — so this renders
// as an honest skeleton (blurred-width bars, not invented text) rather than
// fabricated slang.
function RoomVocabularyTeaser() {
  const t = useTranslations("memory");
  const widths = [64, 40, 88, 52, 30, 72, 46];
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0 pb-2">
        <div className="flex items-center gap-2">
          <Hash className="h-4 w-4 text-text-muted" />
          <CardTitle className="text-[13px]">{t("roomVocabularyTitle")}</CardTitle>
        </div>
        <Badge variant="designed">{t("badgePlanned")}</Badge>
      </CardHeader>
      <CardContent className="pt-0">
        <p className="mb-3 text-[12.5px] text-text-secondary">{t("roomVocabularyDek")}</p>
        <div className="flex flex-wrap gap-2">
          {widths.map((w, i) => (
            <span
              key={i}
              className="h-5 animate-pulse rounded-full bg-bg-elevated"
              style={{ width: `${w}px` }}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
