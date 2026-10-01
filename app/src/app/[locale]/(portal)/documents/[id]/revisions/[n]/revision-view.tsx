"use client";

import { useEffect } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft, History as HistoryIcon, RotateCcw } from "lucide-react";
import { Link, useRouter } from "@/i18n/routing";
import { Button } from "@/components/ui/button";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { DocumentView } from "@/components/documents/registry";
import { PrintButton, PrintableDocument } from "@/components/documents/document-actions";
import { isNotFound } from "@/lib/api/client";
import type { DocumentRevision } from "@/lib/api/document-history";
import { useDocumentRevision, useRestoreRevision } from "@/lib/document-history-queries";
import { isOldRevision, restoreProblem, revisionParam } from "@/lib/documents/history";
import { formatRelative } from "@/lib/format";
import { useNow } from "@/hooks/use-now";

// One old revision of a document, read-only (design/artifact §34). The banner says which
// revision this is and that it is not the current one, on screen only; Restore publishes
// it again as a NEW revision (the old ones all stay), and is offered only while the
// document is active. A number that is not a revision answers like an unknown document.
export function RevisionView({ id, n }: { id: string; n: string }) {
  const number = revisionParam(n);
  if (number === null) return <Gone />;
  return <Revision id={id} n={number} />;
}

function Gone() {
  const td = useTranslations("documents");
  return (
    <PageFrame width="narrow">
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
    </PageFrame>
  );
}

function Revision({ id, n }: { id: string; n: number }) {
  const revision = useDocumentRevision(id, n);
  if (revision.isPending) {
    return (
      <PageFrame width="narrow">
        <Skeleton className="mb-3 h-16 rounded-panel" />
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  if (revision.isError && !revision.data) {
    return isNotFound(revision.error) ? (
      <Gone />
    ) : (
      <PageFrame width="narrow">
        <UnavailableState onRetry={() => revision.refetch()} />
      </PageFrame>
    );
  }
  if (!revision.data) return null;
  return <Loaded id={id} data={revision.data} />;
}

function Loaded({ id, data }: { id: string; data: DocumentRevision }) {
  const t = useTranslations("documentViews");
  const locale = useLocale();
  const router = useRouter();
  const now = useNow() || Date.now();
  const restore = useRestoreRevision(id);
  const old = isOldRevision(data.revision, data.currentRevision);
  useRegisterTitle(data.title);

  // The current revision IS the document: open it there, where it can be changed.
  useEffect(() => {
    if (!old) router.replace(`/documents/${id}`);
  }, [old, router, id]);

  const who = data.writtenBy.kind === "person" ? data.writtenBy.name : t("writtenByNora");
  const onRestore = () =>
    restore.mutate(
      { n: data.revision, base: data.currentRevision },
      { onSuccess: () => router.push(`/documents/${id}`) },
    );

  return (
    <PageFrame width="narrow">
      <Link
        href={`/documents/${id}/history`}
        className="-ml-1.5 mb-4 inline-flex h-8 w-fit items-center gap-0.5 rounded-control pr-2 pl-0.5 text-meta font-medium text-ink-muted transition-colors duration-100 hover:bg-hoodie hover:text-ink"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        {t("historyTitle")}
      </Link>
      <div
        role="status"
        data-testid="old-revision-banner"
        className="mb-6 grid gap-3 rounded-panel border border-hairline border-l-4 border-l-caution bg-surface px-4 py-3"
      >
        <p className="text-title font-semibold text-ink">
          {t("oldRevisionBanner", { n: data.revision, total: data.currentRevision })}
        </p>
        <p className="text-meta text-ink-soft">
          {t("revisionMeta", {
            who,
            ago: formatRelative(Date.parse(data.recordedAt), locale, now),
            sha: data.sha256.slice(0, 8),
          })}
        </p>
        {data.state === "archived" ? <p className="text-meta text-ink-soft">{t("restoreArchivedNote")}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Link
            href={`/documents/${id}`}
            className="inline-flex h-9 items-center gap-2 rounded-control border border-seam px-3 text-[0.8125rem] font-semibold text-ink hover:bg-hoodie"
          >
            <HistoryIcon className="size-4" aria-hidden="true" />
            {t("openCurrent")}
          </Link>
          {old && data.state === "active" ? (
            <Button variant="primary" size="sm" disabled={restore.isPending} onClick={onRestore}>
              <RotateCcw aria-hidden="true" />
              {t("restoreRevision", { n: data.revision })}
            </Button>
          ) : null}
          <PrintButton />
        </div>
        {restore.isError ? (
          <p className="text-meta text-danger" role="alert">
            {t(restoreProblem(restore.error))}
          </p>
        ) : null}
      </div>
      <PrintableDocument>
        {/* Paper only: the banner above never prints, so an old revision's printout says
            itself which revision it is, and never passes for the current document. */}
        <p className="mb-4 hidden text-meta text-ink-soft print:block" data-testid="printed-revision">
          {t("printedRevision", { n: data.revision, total: data.currentRevision })}
        </p>
        <DocumentView doc={data.document} documentId={data.id} />
      </PrintableDocument>
    </PageFrame>
  );
}
