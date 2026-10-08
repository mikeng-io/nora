"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, Download, FileArchive, Link2, Search, X } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { artifactDownloadPath, type Artifact } from "@/lib/api/artifacts";
import { typeLabel } from "@/lib/artifacts/list";
import { cn } from "@/lib/cn";
import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_MAX,
  freshness,
  isTyping,
  normalizeSearch,
  pageCount,
  roomName,
} from "@/lib/documents/list";
import { formatBytes, formatDateTime, formatRelative } from "@/lib/format";
import { useArtifacts } from "@/lib/queries";
import { useNow } from "@/hooks/use-now";

// The files Nora sent out of her computer in the rooms you are in, newest first: what
// she attached in a chat and what she linked. A link shows once, as its newest file.
// Only what the viewer may open is listed (Nora decides that, per room, at every read);
// what she keeps for herself is never here. Whatever is on screen while a new list loads
// is said to be old.
export function ArtifactsView() {
  const t = useTranslations("artifacts");
  const now = useNow() || Date.now();
  const [page, setPage] = useState(1);
  const [text, setText] = useState("");
  const query = useDebounced(normalizeSearch(text), SEARCH_DEBOUNCE_MS);
  const list = useArtifacts(page, query);
  const pages = list.data ? pageCount(list.data.total, list.data.pageSize) : 1;
  const state = freshness({
    isPlaceholderData: list.isPlaceholderData,
    isError: list.isError,
    hasData: list.data !== undefined,
  });
  const busy = state === "loading" || isTyping(text, query);
  const search = (next: string) => {
    setText(next.slice(0, SEARCH_MAX));
    setPage(1);
  };

  return (
    <PageFrame>
      <PageHeader title={t("title")} description={t("lede")} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
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
      <div role="status" className="mb-3 min-h-5 text-meta text-ink-muted" data-testid="artifacts-status">
        {busy ? t("loadingList") : null}
      </div>
      {list.isPending ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[76px] rounded-panel" />
          ))}
        </div>
      ) : list.isError && !list.data ? (
        <UnavailableState onRetry={() => list.refetch()} />
      ) : list.data && list.data.artifacts.length === 0 && page === 1 ? (
        query ? (
          <EmptyState art="notebook" title={t("noMatchTitle")}>
            {t("noMatch", { query })}
          </EmptyState>
        ) : (
          <EmptyState art="notebook" title={t("emptyTitle")}>
            {t("empty")}
          </EmptyState>
        )
      ) : list.data ? (
        <div className="grid gap-6">
          {state === "outdated" ? (
            <p className="flex flex-wrap items-center gap-x-3 text-meta text-ink-soft" role="status">
              <strong className="font-semibold text-ink">{t("outdated")}</strong>
              <span>{t("outdatedHint")}</span>
              <Button variant="quiet" size="sm" onClick={() => list.refetch()}>
                {t("retry")}
              </Button>
            </p>
          ) : null}
          <ol
            className={cn("grid gap-2.5 transition-opacity duration-100", busy && "opacity-50")}
            aria-label={t("title")}
            aria-busy={busy}
          >
            {list.data.artifacts.map((a) => (
              <ArtifactRow key={a.id} artifact={a} now={now} />
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

function ArtifactRow({ artifact, now }: { artifact: Artifact; now: number }) {
  const t = useTranslations("artifacts");
  const locale = useLocale();
  const created = Date.parse(artifact.createdAt);
  return (
    <li className="flex items-center gap-2 rounded-panel border border-hairline bg-surface p-3 pr-2 transition-colors duration-100 hover:border-seam">
      <Link href={`/artifacts/${artifact.id}`} className="group flex min-w-0 flex-1 items-center gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-control bg-hoodie text-ink-muted">
          {artifact.kind === "link" ? (
            <Link2 className="size-5" aria-hidden="true" />
          ) : (
            <FileArchive className="size-5" aria-hidden="true" />
          )}
        </span>
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="truncate text-[0.9375rem] font-semibold text-ink">{artifact.title}</span>
          <span className="flex min-w-0 items-center gap-1.5 text-meta text-ink-muted">
            <span className="truncate">{roomName(artifact)}</span>
            <span aria-hidden="true">·</span>
            <span className="shrink-0">{formatBytes(artifact.size)}</span>
            <span aria-hidden="true">·</span>
            <time
              dateTime={artifact.createdAt}
              title={t("sentOn", { date: formatDateTime(created, locale) })}
              className="shrink-0"
            >
              {formatRelative(created, locale, now)}
            </time>
          </span>
        </span>
        <Badge tone="outline">{typeLabel(artifact.mediaType)}</Badge>
      </Link>
      <a
        href={artifactDownloadPath(artifact.id)}
        download
        aria-label={t("downloadNamed", { title: artifact.title })}
        className="grid size-9 shrink-0 place-items-center rounded-control text-ink-muted hover:bg-hoodie hover:text-ink"
      >
        <Download className="size-4" aria-hidden="true" />
      </a>
    </li>
  );
}
