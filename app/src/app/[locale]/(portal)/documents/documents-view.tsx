"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, FileText } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import type { DocumentSummary } from "@/lib/api/documents";
import { formatRelative } from "@/lib/format";
import { isRevised, pageCount, platformLabel } from "@/lib/documents/list";
import { useDocuments } from "@/lib/queries";
import { useNow } from "@/hooks/use-now";

// What Nora has written up for the rooms you are in, newest first. A document is
// made in a chat; this is where it is read. Only what the viewer may open is listed
// (Nora decides that, per person and per room), so there is nothing here to filter.
export function DocumentsView() {
  const t = useTranslations("documents");
  const now = useNow() || Date.now();
  const [page, setPage] = useState(1);
  const docs = useDocuments(page);
  const pages = docs.data ? pageCount(docs.data.total, docs.data.pageSize) : 1;

  return (
    <PageFrame width="narrow">
      <PageHeader title={t("title")} description={t("lede")} />
      {docs.isPending ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[76px] rounded-panel" />
          ))}
        </div>
      ) : docs.isError && !docs.data ? (
        <UnavailableState onRetry={() => docs.refetch()} />
      ) : docs.data && docs.data.documents.length === 0 && page === 1 ? (
        <EmptyState art="notebook" title={t("emptyTitle")}>
          {t("empty")}
        </EmptyState>
      ) : docs.data ? (
        <div className="grid gap-6">
          <ol className="grid gap-2.5" aria-label={t("title")}>
            {docs.data.documents.map((d) => (
              <DocumentRow key={d.id} doc={d} now={now} />
            ))}
          </ol>
          {pages > 1 ? (
            <nav aria-label={t("pages")} className="flex items-center justify-between gap-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft aria-hidden="true" />
                {t("previous")}
              </Button>
              <span className="text-meta text-ink-muted">{t("page", { page, pages })}</span>
              <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
                {t("next")}
                <ChevronRight aria-hidden="true" />
              </Button>
            </nav>
          ) : null}
        </div>
      ) : null}
    </PageFrame>
  );
}

function DocumentRow({ doc, now }: { doc: DocumentSummary; now: number }) {
  const t = useTranslations("documents");
  const locale = useLocale();
  return (
    <li>
      <Link
        href={`/documents/${doc.id}`}
        className="group flex items-center gap-4 rounded-panel border border-hairline bg-surface p-3 pr-4 transition-colors duration-100 hover:border-seam"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-control bg-hoodie text-ink-muted">
          <FileText className="size-5" aria-hidden="true" />
        </span>
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate text-[0.9375rem] font-semibold text-ink">{doc.title}</span>
          <span className="flex min-w-0 items-center gap-1.5 text-meta text-ink-muted">
            <span className="truncate">
              {t("byIn", { name: doc.createdBy.name, platform: platformLabel(doc.platform) })}
            </span>
            <span aria-hidden="true">·</span>
            <time dateTime={doc.updatedAt} className="shrink-0">
              {formatRelative(Date.parse(doc.updatedAt), locale, now)}
            </time>
          </span>
        </span>
        {isRevised(doc.revision) ? <Badge>{t("revisionN", { n: doc.revision })}</Badge> : null}
        <ChevronRight
          className="hidden size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5 sm:block"
          aria-hidden="true"
        />
      </Link>
    </li>
  );
}
