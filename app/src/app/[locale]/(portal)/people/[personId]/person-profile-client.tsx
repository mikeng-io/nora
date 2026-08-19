"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowLeft, Gamepad2, Sparkles, WifiOff } from "lucide-react";
import { Link } from "@/i18n/routing";
import { fetchPersonProfile, type PersonProfile } from "@/lib/persons-schema";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { PersonAvatar } from "@/components/ui/person-avatar";

// Honcho's peer_card facts are "CATEGORY: Key: Value" — same split the
// standalone Memory page uses (memory/page.tsx's splitFact), duplicated
// here rather than imported since that page's copy isn't exported and
// this is the only other caller.
function splitFact(fact: string): { category: string | null; text: string } {
  const i = fact.indexOf(":");
  if (i < 0) return { category: null, text: fact };
  return { category: fact.slice(0, i).trim(), text: fact.slice(i + 1).trim() };
}

export function PersonProfileClient(
  { personId, isOwnProfile }: { personId: string; isOwnProfile: boolean },
) {
  const t = useTranslations("people");
  const [profile, setProfile] = useState<PersonProfile | null | undefined>(null);
  const [error, setError] = useState(false);
  const [memory, setMemory] = useState<MemoryEntry | null>(null);
  const [memoryError, setMemoryError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPersonProfile(personId)
      .then((p) => {
        if (!cancelled) setProfile(p ?? undefined);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [personId]);

  // Private — the underlying /api/memory route derives the caller's OWN
  // personId from the session server-side (it takes no personId param), so
  // this can only ever return the viewer's own memory regardless of which
  // profile they're looking at; only fetched at all when this IS their own.
  useEffect(() => {
    if (!isOwnProfile) return;
    let cancelled = false;
    fetchMemory()
      .then((entries) => {
        if (!cancelled) setMemory(entries[0] ?? null);
      })
      .catch(() => {
        if (!cancelled) setMemoryError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isOwnProfile]);

  return (
    <div>
      <Link
        href="/people"
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      {error && <Alert variant="destructive">{t("error")}</Alert>}
      {!error && profile === null && <p className="text-text-muted">{t("loading")}</p>}
      {!error && profile === undefined && <Alert variant="destructive">{t("notFound")}</Alert>}

      {profile && (
        <>
          <div className="mb-6 flex items-center gap-3">
            <PersonAvatar
              seed={profile.personId} size={48} className="rounded-full overflow-hidden"
            />
            <div className="flex items-center gap-2.5">
              <h1 className="text-[24px] font-medium tracking-tight">{profile.displayName}</h1>
              {isOwnProfile && <Badge variant="live">{t("youBadge")}</Badge>}
            </div>
          </div>

          <Card className="mb-6">
            <CardHeader className="flex-row items-center gap-2 space-y-0 p-4 pb-2">
              <Gamepad2 className="h-4 w-4 text-text-muted" />
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("gameProfilesLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              {profile.gameProfiles.length === 0 && (
                <p className="text-[13px] text-text-muted">{t("noGameProfilesYet")}</p>
              )}
              {profile.gameProfiles.length > 0 && (
                <ul className="space-y-1.5">
                  {profile.gameProfiles.map((gp) => (
                    <li key={gp.serverId}>
                      <Link
                        href={`/games/${gp.serverId}`}
                        className="text-[13.5px] text-accent hover:underline"
                      >
                        {gp.displayName}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {isOwnProfile && (
            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0 p-4 pb-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-text-muted" />
                  <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                    {t("yourMemoryLabel")}
                  </CardTitle>
                </div>
                <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">
                  {t("memoryPrivateNote")}
                </span>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                {memoryError && (
                  <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-danger">
                    <WifiOff className="h-3.5 w-3.5" />
                    {t("error")}
                  </p>
                )}
                {!memoryError && memory && !memory.reachable && (
                  <p className="flex items-center gap-1.5 font-mono text-[12.5px] text-danger">
                    <WifiOff className="h-3.5 w-3.5" />
                    {t("error")}
                  </p>
                )}
                {!memoryError && memory?.reachable && memory.identity.length > 0 && (
                  <div className="mb-3 flex flex-wrap gap-1.5">
                    {memory.identity.map((fact, i) => {
                      const { category, text } = splitFact(fact);
                      return (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1.5 rounded-full border border-border-strong px-2.5 py-1 text-[11.5px]"
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
                {!memoryError && memory?.reachable && memory.episodes.length > 0 && (
                  <ul className="space-y-2 border-l-2 border-border-strong pl-4">
                    {memory.episodes.map((ep, i) => (
                      <li key={i} className="text-[13.5px] text-text-secondary">
                        {ep}
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
