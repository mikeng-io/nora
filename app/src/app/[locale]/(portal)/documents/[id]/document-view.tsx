"use client";

import { useEffect, useReducer, useRef } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Archive, ArchiveRestore, ChevronLeft, Trash2 } from "lucide-react";
import { Link, useRouter } from "@/i18n/routing";
import { Button } from "@/components/ui/button";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { DocumentView } from "@/components/documents/registry";
import { isNotFound } from "@/lib/api/client";
import type { DocumentDetail } from "@/lib/api/documents";
import { CONFIRM_CLOSED, confirmKey, confirmReducer } from "@/lib/documents/lifecycle";
import { DELETED_FLAG, freshness, isRevised, roomName } from "@/lib/documents/list";
import { documentTabTitle } from "@/lib/documents/title";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useDocument, useSetDocumentState } from "@/lib/queries";
import { useNow } from "@/hooks/use-now";

// One document, drawn from its stored spec by the same registry the fixtures use, in
// the portal's own theme. Unreadable and unknown read the same here because Nora answers
// them the same: an id never confirms that a document exists.
//
// The title is the first thing on the page. The meta line and the archive and delete
// controls come right after it (passed to the renderer as `chrome`), never above it.
export function DocumentPageView({ id }: { id: string }) {
  const t = useTranslations("documents");
  const doc = useDocument(id);
  // The browser tab names the document, not the section; until it is known it says the
  // section, as the server rendered it.
  useTabTitle(doc.data?.title, t("title"));

  if (doc.isPending) {
    return (
      <PageFrame>
        <Skeleton className="mb-3 h-10 w-2/3" />
        <Skeleton className="mb-8 h-5 w-1/2" />
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  if (doc.isError && !doc.data) {
    return (
      <PageFrame>
        {isNotFound(doc.error) ? (
          <EmptyState
            art="lost"
            title={t("notFoundTitle")}
            action={
              <Link href="/documents" className="text-meta font-semibold text-denim hover:underline">
                {t("back")}
              </Link>
            }
          >
            {t("notFound")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => doc.refetch()} />
        )}
      </PageFrame>
    );
  }
  if (!doc.data) return null;
  const outdated = freshness({ isError: doc.isError, hasData: true }) === "outdated";
  return <Loaded doc={doc.data} outdated={outdated} onRetry={() => doc.refetch()} />;
}

/** Sets the tab's title while the page is mounted and puts the server's back after. */
function useTabTitle(title: string | undefined, fallback: string) {
  const text = documentTabTitle(title, fallback);
  useEffect(() => {
    const before = document.title;
    document.title = text;
    return () => {
      document.title = before;
    };
  }, [text]);
}

function Loaded({
  doc,
  outdated,
  onRetry,
}: {
  doc: DocumentDetail;
  outdated: boolean;
  onRetry: () => void;
}) {
  const t = useTranslations("documents");
  const locale = useLocale();
  const now = useNow() || Date.now();
  // The spec's own Page draws the one <h1>; this only tells the phone's top bar the title.
  useRegisterTitle(doc.title);
  const updated = Date.parse(doc.updatedAt);
  const ago = formatRelative(updated, locale, now);
  const who = { name: doc.createdBy.name, room: roomName(doc) };
  const chrome = (
    <div className="flex flex-col gap-3">
      <p
        className="text-meta text-ink-muted"
        data-testid="document-meta"
        title={t("updatedOn", { date: formatDateTime(updated, locale) })}
      >
        {isRevised(doc.revision) ? t("madeRevised", { ...who, n: doc.revision, ago }) : t("made", who)}
      </p>
      {outdated ? (
        <p className="flex flex-wrap items-center gap-x-3 text-meta text-ink-soft" role="status" data-testid="document-outdated">
          <strong className="font-semibold text-ink">{t("outdated")}</strong>
          <span>{t("outdatedHint")}</span>
          <Button variant="quiet" size="sm" onClick={onRetry}>
            {t("retry")}
          </Button>
        </p>
      ) : null}
      <Lifecycle doc={doc} />
    </div>
  );
  return (
    <PageFrame>
      <Link
        href="/documents"
        className="-ml-1.5 mb-4 inline-flex h-8 w-fit items-center gap-0.5 rounded-control pr-2 pl-0.5 text-meta font-medium text-ink-muted transition-colors duration-100 hover:bg-hoodie hover:text-ink"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        {t("back")}
      </Link>
      <DocumentView doc={doc.document} documentId={doc.id} chrome={chrome} />
    </PageFrame>
  );
}

// Archive, delete (after a confirm) or restore. Each one is recorded by Nora before the
// page changes: nothing here is optimistic. A delete leaves the page for the list, which
// says it can be restored by asking Nora in the chat, because the document no longer
// opens for anyone.
function Lifecycle({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents");
  const router = useRouter();
  const change = useSetDocumentState(doc.id);
  const [confirm, dispatch] = useReducer(confirmReducer, CONFIRM_CLOSED);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const archived = doc.state === "archived";

  // Focus follows the panel: into it when it opens, back to the Delete button when it is
  // cancelled (the reducer says which; `lib/documents/lifecycle.ts`).
  useEffect(() => {
    if (confirm.focus === "confirm") cancel.current?.focus();
    else if (confirm.focus === "trigger") trigger.current?.focus();
  }, [confirm]);

  const set = (state: "active" | "archived" | "deleted") =>
    change.mutate(state, {
      onSuccess: () => {
        dispatch("done");
        if (state === "deleted") router.push(`/documents?${DELETED_FLAG}=1`);
      },
    });

  return (
    <div className="grid gap-3" data-testid="document-lifecycle">
      {archived ? (
        <p className="text-meta text-ink-soft" role="status">
          {t("archivedNote")}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {archived ? (
          <Button variant="outline" size="sm" disabled={change.isPending} onClick={() => set("active")}>
            <ArchiveRestore aria-hidden="true" />
            {t("restore")}
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled={change.isPending} onClick={() => set("archived")}>
            <Archive aria-hidden="true" />
            {t("archive")}
          </Button>
        )}
        {confirm.open ? null : (
          <Button
            ref={trigger}
            variant="destructive"
            size="sm"
            disabled={change.isPending}
            onClick={() => dispatch("ask")}
          >
            <Trash2 aria-hidden="true" />
            {t("delete")}
          </Button>
        )}
      </div>
      {confirm.open ? (
        <div
          role="alertdialog"
          aria-labelledby="delete-title"
          aria-describedby="delete-body"
          className="grid gap-3 rounded-panel border border-hairline bg-surface p-4"
          onKeyDown={(e) => {
            const event = confirmKey(e.key);
            if (event) {
              e.stopPropagation();
              dispatch(event);
            }
          }}
        >
          <p id="delete-title" className="font-semibold text-ink">
            {t("deleteConfirmTitle")}
          </p>
          <p id="delete-body" className="text-meta text-ink-soft">
            {t("deleteConfirm")}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="destructive" size="sm" disabled={change.isPending} onClick={() => set("deleted")}>
              <Trash2 aria-hidden="true" />
              {t("deleteConfirmAction")}
            </Button>
            <Button
              ref={cancel}
              variant="quiet"
              size="sm"
              disabled={change.isPending}
              onClick={() => dispatch("cancel")}
            >
              {t("cancel")}
            </Button>
          </div>
        </div>
      ) : null}
      {change.isError ? (
        <p className="text-meta text-danger" role="alert">
          {t("actionFailed")}
        </p>
      ) : null}
    </div>
  );
}
