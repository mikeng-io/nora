"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, FileText, Search, X } from "lucide-react";
import { Link, useRouter } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import type { DocumentFilter, DocumentSummary } from "@/lib/api/documents";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_MAX,
  deletedNoticeShown,
  freshness,
  isRevised,
  isTyping,
  normalizeSearch,
  pageCount,
  roomName,
} from "@/lib/documents/list";
import { useDocuments } from "@/lib/queries";
import { useNow } from "@/hooks/use-now";

// What Nora has written up for the rooms you are in, newest first. A document is
// made in a chat; this is where it is read. Only what the viewer may open is listed
// (Nora decides that, per person and per room). Two controls narrow it: Current or
// Archived, and a search over titles and text (a deleted document is in neither, because
// Nora never sends one). Whatever is on screen while a new list loads is said to be old.
export function DocumentsView() {
  const t = useTranslations("documents");
  const now = useNow() || Date.now();
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<DocumentFilter>("active");
  const [text, setText] = useState("");
  const query = useDebounced(normalizeSearch(text), SEARCH_DEBOUNCE_MS);
  const docs = useDocuments(page, filter, query);
  const pages = docs.data ? pageCount(docs.data.total, docs.data.pageSize) : 1;
  const state = freshness({
    isPlaceholderData: docs.isPlaceholderData,
    isError: docs.isError,
    hasData: docs.data !== undefined,
  });
  // The rows are an answer to an older question while the person is typing or the new
  // list is on its way: they must not look current.
  const busy = state === "loading" || isTyping(text, query);
  const choose = (next: DocumentFilter) => {
    setFilter(next);
    setPage(1);
  };
  const search = (next: string) => {
    setText(next.slice(0, SEARCH_MAX));
    setPage(1);
  };

  return (
    <PageFrame>
      <PageHeader title={t("title")} description={t("lede")} />
      <DeletedNotice />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div
          role="group"
          aria-label={t("filterLabel")}
          className="inline-flex gap-0.5 rounded-control border border-hairline bg-hoodie p-0.5"
        >
          {(["active", "archived"] as const).map((f) => (
            <Button
              key={f}
              size="sm"
              variant="quiet"
              aria-pressed={filter === f}
              className={cn(
                filter === f && "bg-ink text-studio hover:bg-ink hover:text-studio",
              )}
              onClick={() => choose(f)}
            >
              {f === "active" ? t("filterActive") : t("filterArchived")}
            </Button>
          ))}
        </div>
        <div className="relative min-w-0 flex-1 sm:max-w-sm">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-muted"
            aria-hidden="true"
          />
          <input
            type="search"
            value={text}
            onChange={(e) => search(e.target.value)}
            aria-label={t("searchLabel")}
            placeholder={t("searchPlaceholder")}
            maxLength={SEARCH_MAX}
            autoComplete="off"
            className="h-9 w-full rounded-control border border-hairline bg-surface pr-9 pl-9 text-[0.8125rem] text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-denim [&::-webkit-search-cancel-button]:hidden"
          />
          {text ? (
            <button
              type="button"
              aria-label={t("searchClear")}
              onClick={() => search("")}
              className="absolute top-1/2 right-1.5 grid size-6 -translate-y-1/2 place-items-center rounded-control text-ink-muted hover:bg-hoodie hover:text-ink"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>
      <div role="status" className="mb-3 min-h-5 text-meta text-ink-muted" data-testid="documents-status">
        {busy ? t("loadingList") : null}
      </div>
      {docs.isPending ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[76px] rounded-panel" />
          ))}
        </div>
      ) : docs.isError && !docs.data ? (
        <UnavailableState onRetry={() => docs.refetch()} />
      ) : docs.data && docs.data.documents.length === 0 && page === 1 ? (
        query ? (
          <EmptyState art="notebook" title={t("noMatchTitle")}>
            {t("noMatch", { query })}
          </EmptyState>
        ) : filter === "archived" ? (
          <EmptyState art="notebook" title={t("archivedEmptyTitle")}>
            {t("archivedEmpty")}
          </EmptyState>
        ) : (
          <EmptyState art="notebook" title={t("emptyTitle")}>
            {t("empty")}
          </EmptyState>
        )
      ) : docs.data ? (
        <div className="grid gap-6">
          {state === "outdated" ? (
            <p
              className="flex flex-wrap items-center gap-x-3 text-meta text-ink-soft"
              role="status"
              data-testid="documents-outdated"
            >
              <strong className="font-semibold text-ink">{t("outdated")}</strong>
              <span>{t("outdatedHint")}</span>
              <Button variant="quiet" size="sm" onClick={() => docs.refetch()}>
                {t("retry")}
              </Button>
            </p>
          ) : null}
          <ol
            className={cn("grid gap-2.5 transition-opacity duration-100", busy && "opacity-50")}
            aria-label={t("title")}
            aria-busy={busy}
          >
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

/** The value, once it has stopped changing for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (Object.is(value, settled)) return;
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, settled, ms]);
  return settled;
}

// A delete leaves the page it was made on and lands here with `?deleted=1`: the one place
// to say it can be restored by asking Nora in the chat, because nothing in the portal can.
function DeletedNotice() {
  const t = useTranslations("documents");
  const router = useRouter();
  const params = useSearchParams();
  if (!deletedNoticeShown(params.toString())) return null;
  return (
    <div
      role="status"
      data-testid="documents-deleted"
      className="mb-4 flex items-start justify-between gap-3 rounded-panel border border-hairline bg-surface px-4 py-3 text-meta text-ink-soft"
    >
      <p>{t("deletedNotice")}</p>
      <Button variant="quiet" size="sm" onClick={() => router.replace("/documents")}>
        {t("dismiss")}
      </Button>
    </div>
  );
}

function DocumentRow({ doc, now }: { doc: DocumentSummary; now: number }) {
  const t = useTranslations("documents");
  const locale = useLocale();
  const updated = Date.parse(doc.updatedAt);
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
            <span className="truncate">{t("byIn", { name: doc.createdBy.name, room: roomName(doc) })}</span>
            <span aria-hidden="true">·</span>
            <time
              dateTime={doc.updatedAt}
              title={t("updatedOn", { date: formatDateTime(updated, locale) })}
              className="shrink-0"
            >
              {formatRelative(updated, locale, now)}
            </time>
          </span>
        </span>
        {doc.state === "archived" ? <Badge tone="outline">{t("archived")}</Badge> : null}
        {isRevised(doc.revision) ? <Badge>{t("revisionN", { n: doc.revision })}</Badge> : null}
        <ChevronRight
          className="hidden size-4 shrink-0 text-ink-muted transition-transform duration-160 ease-nora group-hover:translate-x-0.5 sm:block"
          aria-hidden="true"
        />
      </Link>
    </li>
  );
}
