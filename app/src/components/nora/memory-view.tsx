"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { CircleAlert, Lock, Search } from "lucide-react";
import { useMemory } from "@/lib/queries";
import { orderEpisodes, type Episode } from "@/lib/memory";
import { dateFormat } from "@/lib/format";
import type { ProfileAccount, ProfileEvidence, ProfileLine } from "@/lib/api/nora";
import { NoraAvatar } from "@/components/ui/avatar";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Button } from "@/components/ui/button";

const PAGE = 12;

// What Nora remembers about the viewer, and only the viewer: the proxy
// fixes the person to the session (nora/web/memory.py). Two reads that
// never collapse into one list: who you are to her (identity claims) and
// what happened (episodes, laid along a line when they carry a date).
export function MemoryView({ compact = false }: { compact?: boolean }) {
  const t = useTranslations("memory");
  const memory = useMemory();
  // One entry per Nora workspace the viewer belongs to (DEC-214): her memory
  // of you in one circle is never merged with another's.
  const entries = memory.data ?? [];
  const [picked, setPicked] = useState(0);
  const entry = entries[Math.min(picked, Math.max(entries.length - 1, 0))];
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE);

  const episodes = useMemo(() => orderEpisodes(entry?.episodes ?? []), [entry]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? episodes.filter((e) => e.raw.toLowerCase().includes(q)) : episodes;
  }, [episodes, query]);

  if (memory.isPending) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-24 rounded-panel" />
        <Panel className="p-5">
          <SkeletonText lines={5} />
        </Panel>
      </div>
    );
  }
  if (memory.isError && !memory.data) return <UnavailableState onRetry={() => memory.refetch()} />;
  if (!entry || (entry.identity.length === 0 && entry.episodes.length === 0)) {
    return (
      <EmptyState art="notebook" title={t("emptyTitle")}>
        {entry && !entry.reachable ? t("unreachable") : t("empty")}
      </EmptyState>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] lg:items-start lg:gap-6">
      <div className="grid gap-4">
        {entries.length > 1 ? (
          <div role="tablist" aria-label={t("workspaces")} className="flex flex-wrap gap-2">
            {entries.map((e, i) => (
              <Button
                key={e.workspace ?? i}
                role="tab"
                aria-selected={entry === e}
                variant={entry === e ? "outline" : "quiet"}
                size="sm"
                onClick={() => {
                  setPicked(i);
                  setQuery("");
                  setShown(PAGE);
                }}
              >
                {e.workspace ?? String(i + 1)}
              </Button>
            ))}
          </div>
        ) : null}
        <div className="flex items-start gap-3 rounded-panel border border-hairline bg-surface p-4">
          <Lock className="mt-0.5 size-4 shrink-0 text-ink-muted" aria-hidden="true" />
          <p className="text-meta text-ink-soft">{t("private")}</p>
        </div>
        {!entry.reachable ? (
          <div role="status" className="flex items-start gap-3 rounded-panel border border-hairline bg-surface p-4">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
            <p className="text-meta text-ink-soft">{t("partial")}</p>
          </div>
        ) : null}
        <Panel aria-labelledby="identity-title">
          <PanelHeader id="identity-title" title={t("identityTitle")} description={t("identityLede")} />
          {entry.identity.length ? (
            <dl className="divide-y divide-hairline border-t border-hairline">
              {entry.identity.map((line, i) => {
                const m = /^([^:：]{1,32})[:：]\s*(.+)$/.exec(line);
                return (
                  <div key={i} className="grid gap-0.5 px-4 py-3 sm:px-5">
                    {m ? (
                      <>
                        <dt className="text-label text-ink-muted">{m[1]}</dt>
                        <dd className="text-body text-ink">{m[2]}</dd>
                      </>
                    ) : (
                      <dd className="text-body text-ink">{line}</dd>
                    )}
                  </div>
                );
              })}
            </dl>
          ) : (
            <PanelBody>
              <p className="text-body text-ink-soft">{t("identityEmpty")}</p>
            </PanelBody>
          )}
        </Panel>
        {entry.profile?.map((account) => (
          <ProfileAccountPanel key={account.unit} account={account} />
        ))}
      </div>

      <Panel aria-labelledby="episodes-title">
        <PanelHeader
          id="episodes-title"
          title={t("episodesTitle")}
          description={t("episodesLede", { count: episodes.length })}
        />
        {episodes.length > 4 && !compact ? (
          <div className="px-4 pb-3 sm:px-5">
            <label className="flex h-11 items-center gap-2 rounded-control border border-hairline bg-studio px-3 focus-within:border-seam focus-within:ring-2 focus-within:ring-denim lg:h-10">
              <Search className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
              <span className="sr-only">{t("search")}</span>
              <input
                type="search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setShown(PAGE);
                }}
                placeholder={t("search")}
                className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-ink-muted"
              />
            </label>
          </div>
        ) : null}
        {filtered.length === 0 ? (
          <PanelBody>
            <p className="text-body text-ink-soft">{query ? t("noMatch") : t("episodesEmpty")}</p>
          </PanelBody>
        ) : (
          <>
            <EpisodeLine episodes={filtered.slice(0, compact ? 5 : shown)} />
            {!compact && filtered.length > shown ? (
              <div className="border-t border-hairline px-4 py-3 sm:px-5">
                <Button variant="quiet" size="sm" onClick={() => setShown((s) => s + PAGE)}>
                  {t("more", { count: Math.min(PAGE, filtered.length - shown) })}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Panel>
    </div>
  );
}

/** One Profile account of the viewer (45, the switch): every chain head with
 * its state, the evidence behind each line, and the open disputes — the
 * correction surface (cognition-loop/50 §4). */
function ProfileAccountPanel({ account }: { account: ProfileAccount }) {
  const t = useTranslations("memory");
  const locale = useLocale();
  const fmt = (ms: number) =>
    dateFormat(locale, { month: "short", day: "numeric", year: "numeric" }).format(ms);
  const stateLabel = (state: string) =>
    state === "current" ? null : (
      <span className="w-fit rounded-full border border-hairline px-2 py-0.5 text-label text-ink-muted">
        {t(`state_${state}`)}
      </span>
    );
  const section = (title: string, lines: ProfileLine[]) =>
    lines.length ? (
      <section className="grid gap-2 px-4 py-3 sm:px-5">
        <h3 className="text-label text-ink-muted">{title}</h3>
        <ul className="grid gap-3">
          {lines.map((line, i) => (
            <li key={i} className="grid gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-body text-ink">{line.text}</p>
                {stateLabel(line.state)}
              </div>
              <p className="text-meta text-ink-muted">
                {fmt(line.written_at)}
              </p>
              {line.part === "guesses" && line.prediction ? (
                <p className="text-meta text-ink-soft">
                  {t("profilePrediction", { prediction: line.prediction })}
                  {line.prediction_window_end
                    ? ` ${t("profileWindow", { date: fmt(line.prediction_window_end) })}`
                    : ""}
                  {line.verdict && line.verdict !== "open"
                    ? ` ${t(`verdict_${line.verdict}`)}`
                    : ""}
                </p>
              ) : null}
              {line.evidence.length ? (
                <ul className="grid gap-0.5">
                  {line.evidence.map((ev, j) => (
                    <li key={j} className="text-meta text-ink-muted">
                      <EvidenceLine evidence={ev} fmt={fmt} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    ) : null;
  return (
    <Panel aria-labelledby={`profile-title-${account.unit}`}>
      <PanelHeader
        id={`profile-title-${account.unit}`}
        title={t("profileTitle")}
        description={t("profileLede")}
      />
      <div className="grid gap-1 divide-y divide-hairline border-t border-hairline">
        {account.card && <p className="whitespace-pre-line text-body">{account.card}</p>}
        {section(t("profileIdentity"), account.lines.filter((l) => l.part === "facts"))}
        {section(t("profileCharacter"), account.lines.filter((l) => l.part === "traits"))}
        {section(t("profileQuestions"), account.lines.filter((l) => l.part === "guesses"))}
        {account.disputes.length ? (
          <section className="grid gap-2 px-4 py-3 sm:px-5">
            <h3 className="text-label text-ink-muted">{t("profileDisputes")}</h3>
            <ul className="grid gap-2">
              {account.disputes.map((d, i) => (
                <li key={i} className="text-body text-ink-soft">
                  {t(`dispute_${d.kind}`)} — “{d.claim}”
                  <span className="text-meta text-ink-muted">
                    {d.disputed_by ? ` · ${d.disputed_by}` : ""} · {fmt(d.written_at)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </Panel>
  );
}

/** The evidence behind one line: the message or episode's text when the
 * viewer may still see it, else an honest note about why not. */
function EvidenceLine({
  evidence,
  fmt,
}: {
  evidence: ProfileEvidence;
  fmt: (ms: number) => string;
}) {
  const t = useTranslations("memory");
  if (evidence.text != null) {
    return (
      <>
        “{evidence.text}”
        {evidence.by ? ` — ${evidence.by}` : ""}
        {evidence.said_at ? `, ${fmt(evidence.said_at)}` : ""}
      </>
    );
  }
  if (evidence.kind === "social") return <>{t("evidenceSocial")}</>;
  if (evidence.kind === "rollup") return <>{t("evidenceRollup")}</>;
  if (evidence.kind === "unknown") return <>{t("evidenceUnknown")}</>;
  return <>{t("evidenceGone")}</>;
}

/** Episodes along one thin line: her words on blush wash (they are hers),
 * each dated one with its date on the line. */
function EpisodeLine({ episodes }: { episodes: Episode[] }) {
  const locale = useLocale();
  const fmt = (d: string) =>
    dateFormat(locale, { month: "short", day: "numeric", year: "numeric" }).format(
      Date.parse(`${d}T12:00:00+08:00`),
    );
  return (
    <ol className="relative grid gap-3 border-t border-hairline px-4 py-4 sm:px-5">
      <span className="absolute top-4 bottom-4 left-[27px] w-px bg-hairline sm:left-[31px]" aria-hidden="true" />
      {episodes.map((e, i) => (
        <li key={i} className="relative grid grid-cols-[24px_minmax(0,1fr)] gap-3">
          <span className="relative z-10 mt-1 grid size-6 place-items-center rounded-full bg-surface">
            {i === 0 ? <NoraAvatar size={22} /> : <span className="size-2 rounded-full bg-blush" aria-hidden="true" />}
          </span>
          <div className="grid gap-1">
            {e.date ? <time className="font-mono text-label text-ink-muted tnum" dateTime={e.date}>{fmt(e.date)}</time> : null}
            <p className="w-fit max-w-[62ch] rounded-panel rounded-tl-[6px] bg-blush-wash px-3.5 py-2 text-body text-ink">
              {e.text}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
