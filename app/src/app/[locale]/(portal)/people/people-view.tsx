"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { ChevronRight } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { useViewer } from "@/components/shell/viewer";
import { PageHeader } from "@/components/ui/page-header";
import { PersonAvatar } from "@/components/ui/avatar";
import { Badge, Dot } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { PresenceIcon, PresenceMeta, usePresenceWords } from "@/components/presence/presence-text";
import { useDerivedPlaces, useRoster, useSavedPlaces } from "@/lib/queries";
import type { PresenceEntry } from "@/lib/api/presence";
import type { PersonSummary } from "@/lib/api/people";
import { isLive, kindRank, presenceKind } from "@/lib/presence";
import { personStyle } from "@/lib/person";
import { cn } from "@/lib/cn";
import { usePresenceNow } from "@/hooks/use-presence";

// Everyone Nora knows (identity.toml's roster), each with where they are
// if they share it. The viewer first, then whoever is around, then the
// rest by name.
export function PeopleView() {
  const t = useTranslations("people");
  const viewer = useViewer();
  const roster = useRoster();
  const presence = usePresenceNow();

  const people = useMemo(() => {
    const byId = new Map((presence.data ?? []).map((p) => [p.personId, p]));
    return (roster.data ?? [])
      .map((p) => ({ person: p, presence: byId.get(p.personId) ?? null }))
      .sort((a, b) => {
        if (a.person.personId === viewer.personId) return -1;
        if (b.person.personId === viewer.personId) return 1;
        const ra = a.presence ? kindRank(presenceKind(a.presence)) : 99;
        const rb = b.presence ? kindRank(presenceKind(b.presence)) : 99;
        return ra - rb || a.person.displayName.localeCompare(b.person.displayName);
      });
  }, [roster.data, presence.data, viewer.personId]);

  return (
    <PageFrame>
      <PageHeader title={t("title")} description={t("lede")} />
      {roster.isPending ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <li key={i}>
              <Skeleton className="h-[92px] rounded-panel" />
            </li>
          ))}
        </ul>
      ) : roster.isError && !roster.data ? (
        <UnavailableState onRetry={() => roster.refetch()} />
      ) : people.length === 0 ? (
        <EmptyState art="quiet">{t("empty")}</EmptyState>
      ) : (
        <ul className="stagger grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {people.map(({ person, presence: pr }) => (
            <PersonCard key={person.personId} person={person} presence={pr} you={person.personId === viewer.personId} />
          ))}
        </ul>
      )}
    </PageFrame>
  );
}

function PersonCard({
  person,
  presence,
  you,
}: {
  person: PersonSummary;
  presence: PresenceEntry | null;
  you: boolean;
}) {
  const t = useTranslations("people");
  return (
    <li>
      <Link
        href={`/people/${person.personId}`}
        className="group flex items-center gap-3.5 rounded-panel border border-hairline bg-surface p-4 transition-colors duration-100 hover:border-seam"
        style={personStyle(person.personId)}
      >
        <PersonAvatar id={person.personId} name={person.displayName} size={48} />
        <span className="grid min-w-0 flex-1 gap-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[1rem] font-semibold text-ink">{person.displayName}</span>
            {you ? <Badge tone="denim">{t("you")}</Badge> : null}
          </span>
          {presence ? (
            <PresenceLine entry={presence} />
          ) : (
            <span className="text-meta text-ink-muted">{t("notInCircle")}</span>
          )}
        </span>
        <ChevronRight
          className="size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5"
          aria-hidden="true"
        />
      </Link>
    </li>
  );
}

function PresenceLine({ entry }: { entry: PresenceEntry }) {
  const saved = useSavedPlaces();
  const derived = useDerivedPlaces();
  const words = usePresenceWords(
    entry,
    saved.data,
    derived.data?.subjects.find((s) => s.subjectId === entry.personId)?.places,
  );
  const live = isLive(words.kind);
  return (
    <span className="grid gap-0.5">
      <span className={cn("flex min-w-0 items-center gap-1.5 text-meta", live ? "text-ink-soft" : "text-ink-muted")}>
        {live ? <Dot tone="person" live /> : <PresenceIcon kind={words.kind} />}
        <span className="truncate">{words.primary}</span>
      </span>
      <PresenceMeta words={words} className="text-label" />
    </span>
  );
}
