"use client";

import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Badge } from "@/components/ui/badge";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { isNotFound } from "@/lib/api/client";
import type { DocumentRevisions, RevisionWriter } from "@/lib/api/document-history";
import { useDocumentRevisions } from "@/lib/document-history-queries";
import { shortSha } from "@/lib/documents/history";
import { formatRelative } from "@/lib/format";
import { useNow } from "@/hooks/use-now";

// A document's history (design/artifact §34): every revision, newest first. Each is a
// full snapshot Nora kept; an old one opens read-only, the current one is the document.
// Unreadable, deleted and unknown read the same here, as on the document page.
export function HistoryView({ id }: { id: string }) {
  const t = useTranslations("documentViews");
  const td = useTranslations("documents");
  const history = useDocumentRevisions(id);

  if (history.isPending) {
    return (
      <PageFrame width="narrow">
        <Skeleton className="mb-3 h-10 w-2/3" />
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  if (history.isError && !history.data) {
    return (
      <PageFrame width="narrow">
        {isNotFound(history.error) ? (
          <EmptyState
            art="lost"
            title={td("notFoundTitle")}
            action={
              <Link href="/documents" className="text-meta font-semibold text-denim hover:underline">
                {td("back")}
              </Link>
            }
          >
            {td("notFound")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => history.refetch()} />
        )}
      </PageFrame>
    );
  }
  if (!history.data) return null;
  return <Loaded id={id} data={history.data} />;
}

function Loaded({ id, data }: { id: string; data: DocumentRevisions }) {
  const t = useTranslations("documentViews");
  const locale = useLocale();
  const now = useNow() || Date.now();
  useRegisterTitle(t("historyTitle"));
  const writer = (w: RevisionWriter) => (w.kind === "person" ? w.name : t("writtenByNora"));
  return (
    <PageFrame width="narrow">
      <Link
        href={`/documents/${id}`}
        className="-ml-1.5 mb-4 inline-flex h-8 w-fit items-center gap-0.5 rounded-control pr-2 pl-0.5 text-meta font-medium text-ink-muted transition-colors duration-100 hover:bg-hoodie hover:text-ink"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        {data.title}
      </Link>
      <h1 className="mb-2 text-display font-semibold text-ink">{t("historyTitle")}</h1>
      <p className="mb-6 text-body text-ink-soft">{t("historyLede", { n: data.revisions.length })}</p>
      <ol
        aria-label={t("historyTitle")}
        className="flex flex-col divide-y divide-hairline rounded-panel border border-hairline bg-surface"
        data-testid="revision-list"
      >
        {data.revisions.map((r) => {
          const current = r.revision === data.revision;
          const href = current ? `/documents/${id}` : `/documents/${id}/revisions/${r.revision}`;
          return (
            <li key={r.revision}>
              <Link
                href={href}
                className="flex flex-col gap-1 px-4 py-3 transition-colors duration-100 hover:bg-hoodie"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-meta text-ink-muted">{t("revisionN", { n: r.revision })}</span>
                  {current ? <Badge tone="denim">{t("currentBadge")}</Badge> : null}
                </span>
                <span className="text-title font-semibold text-ink">{r.title}</span>
                <span className="text-meta text-ink-muted">
                  {t("revisionMeta", {
                    who: writer(r.writtenBy),
                    ago: formatRelative(Date.parse(r.recordedAt), locale, now),
                    sha: shortSha(r.sha256),
                  })}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </PageFrame>
  );
}
