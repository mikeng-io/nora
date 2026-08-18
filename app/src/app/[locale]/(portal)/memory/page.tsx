"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { WifiOff, Sparkles, Hash } from "lucide-react";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";
import { hueOf } from "@/lib/person-color";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PersonAvatar } from "@/components/ui/person-avatar";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";

// Honcho's peer_card facts are observed live as "CATEGORY: Key: Value"
// (e.g. "IDENTITY: Name: Miles Novak (Miles)", "ATTRIBUTE: Workplace: Acme
// Tower") — the leading CATEGORY becomes the small muted label, the "Key:
// Value" remainder stays together as the prominent text (splitting IT
// further would separate "Name" from "Miles Novak" for no benefit).
function splitFact(fact: string): { category: string | null; text: string } {
  const i = fact.indexOf(":");
  if (i < 0) return { category: null, text: fact };
  return { category: fact.slice(0, i).trim(), text: fact.slice(i + 1).trim() };
}

const EPISODES_COLLAPSED_LIMIT = 5;

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
            {entries.map((entry) => (
              <MemoryPersonCard key={entry.person_id} entry={entry} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MemoryPersonCard({ entry }: { entry: MemoryEntry }) {
  const t = useTranslations("memory");
  const [expanded, setExpanded] = useState(false);
  const hue = hueOf(entry.person_id);
  const dotColor = `hsl(${hue} 65% 55%)`;
  const shown = expanded ? entry.episodes : entry.episodes.slice(0, EPISODES_COLLAPSED_LIMIT);
  const hiddenCount = entry.episodes.length - shown.length;

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2.5 space-y-0 pb-2">
        <PersonAvatar seed={entry.person_id} size={32} className="rounded-full overflow-hidden" />
        <CardTitle>{entry.display_name}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {!entry.reachable && (
          <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-danger">
            <WifiOff className="h-3.5 w-3.5" />
            {t("unavailable")}
          </p>
        )}

        {entry.reachable && entry.identity.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-1.5">
            {entry.identity.map((fact, i) => {
              const { category, text } = splitFact(fact);
              return (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px]"
                  style={{ borderColor: `hsl(${hue} 50% 60% / 0.4)` }}
                >
                  {category && (
                    <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">
                      {category}
                    </span>
                  )}
                  <span className="text-text-secondary">{text}</span>
                </span>
              );
            })}
          </div>
        )}

        {entry.reachable && entry.episodes.length === 0 && (
          <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-text-muted">
            <Sparkles className="h-3.5 w-3.5" />
            {t("nothingYet")}
          </p>
        )}

        {entry.reachable && entry.episodes.length > 0 && (
          <>
            <ul className="space-y-2 border-l-2 pl-4" style={{ borderColor: `hsl(${hue} 50% 60% / 0.3)` }}>
              {shown.map((ep, i) => (
                <li key={i} className="relative text-[13.5px] text-text-secondary">
                  <span
                    className="absolute top-[7px] h-1.5 w-1.5 -translate-x-[calc(1rem+3px)] rounded-full"
                    style={{ backgroundColor: dotColor }}
                  />
                  {ep}
                </li>
              ))}
            </ul>
            {(hiddenCount > 0 || expanded) && entry.episodes.length > EPISODES_COLLAPSED_LIMIT && (
              <button
                onClick={() => setExpanded((v) => !v)}
                className="mt-2 pl-4 font-mono text-[11px] uppercase tracking-wide text-accent hover:underline"
              >
                {expanded ? t("showLess") : t("showMore", { count: hiddenCount })}
              </button>
            )}
          </>
        )}
      </CardContent>
    </Card>
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
