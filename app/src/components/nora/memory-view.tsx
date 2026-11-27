"use client";

import { useLocale, useTranslations } from "next-intl";
import { Lock } from "lucide-react";
import { useMemory } from "@/lib/queries";
import { dateFormat } from "@/lib/format";
import type { ProfileAccount, ProfileEvidence, ProfileLine } from "@/lib/api/nora";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";

// Her account of the viewer, and only the viewer: the proxy fixes the person
// to the session (nora/web/memory.py). One panel per unit that lists them,
// shown apart and never merged. A viewer with no account to show gets the
// empty state, which says the page may not show everything she holds.
export function MemoryView() {
  const t = useTranslations("memory");
  const memory = useMemory();
  const accounts = memory.data ?? [];

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
  if (accounts.length === 0) {
    return (
      <EmptyState art="notebook" title={t("emptyTitle")}>
        {t("empty")}
      </EmptyState>
    );
  }

  return (
    <div className="grid max-w-3xl gap-4">
      <div className="flex items-start gap-3 rounded-panel border border-hairline bg-surface p-4">
        <Lock className="mt-0.5 size-4 shrink-0 text-ink-muted" aria-hidden="true" />
        <p className="text-meta text-ink-soft">{t("private")}</p>
      </div>
      {accounts.map((account) => (
        <ProfileAccountPanel key={account.unit} account={account} />
      ))}
    </div>
  );
}

/** One Profile account of the viewer (docs/design/cognition/graph.md): every chain head with
 * its state, the evidence behind each line, and the open disputes — the
 * correction surface (docs/design/cognition/graph.md «Disputes and removal»). */
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
            <li key={line.id ?? i} className="grid gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-body text-ink">{line.text}</p>
                {stateLabel(line.state)}
              </div>
              <p className="text-meta text-ink-muted">
                {fmt(line.written_at)}{line.origin ? ` · ${line.origin}` : ""}{line.id ? ` · ${line.id}` : ""}
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
        {section(t("profileTitle"), account.lines.filter((l) => l.part === "other"))}
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
