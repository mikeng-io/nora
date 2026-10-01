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
import {
  CONFIRM_CLOSED,
  confirmKey,
  confirmReducer,
  type ConfirmState,
} from "@/lib/documents/lifecycle";
import { DELETED_FLAG, detailView, isRevised, roomName } from "@/lib/documents/list";
import { documentTabTitle, shouldReassertTitle, shouldRestoreTitle } from "@/lib/documents/title";
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

  const view = detailView({
    isPending: doc.isPending,
    isError: doc.isError,
    hasData: doc.data !== undefined,
    notFound: isNotFound(doc.error),
  });
  if (view === "loading") {
    return (
      <PageFrame>
        <Skeleton className="mb-3 h-10 w-2/3" />
        <Skeleton className="mb-8 h-5 w-1/2" />
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  if (view === "not-found" || view === "unavailable") {
    return (
      <PageFrame>
        {view === "not-found" ? (
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
  return <Loaded doc={doc.data} outdated={view === "outdated"} onRetry={() => doc.refetch()} />;
}

/** Sets the tab's title while the page is mounted, takes it back if Next writes the route's
 * own title over it while the address is still this document's, and on leaving puts the
 * section's title back unless another page has already set its own
 * (`lib/documents/title.ts` says why each of those is conditional). */
function useTabTitle(title: string | undefined, fallback: string) {
  const text = documentTabTitle(title, fallback);
  useEffect(() => {
    const section = documentTabTitle(null, fallback);
    const path = window.location.pathname;
    document.title = text;
    const watch = new MutationObserver(() => {
      if (shouldReassertTitle(document.title, text, path, window.location.pathname)) {
        document.title = text;
      }
    });
    watch.observe(document.head, { subtree: true, childList: true, characterData: true });
    return () => {
      watch.disconnect();
      if (shouldRestoreTitle(document.title, text)) document.title = section;
    };
  }, [text, fallback]);
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
  const restore = useRef<HTMLButtonElement>(null);
  const archive = useRef<HTMLButtonElement>(null);
  const archived = doc.state === "archived";

  // Focus follows the panel: into it when it opens (and again when a delete fails, so the
  // error is read), back to the Delete button when it is cancelled, and to the Restore or
  // Archive button that replaced the one just pressed (the reducer says which;
  // `lib/documents/lifecycle.ts`). `archived` is a dependency because the swapped button
  // only exists once the page has re-read the document.
  const applied = useRef<ConfirmState | null>(null);
  useEffect(() => {
    // Each focus request is applied once, when its button exists: a later re-read of the
    // document must not pull focus back to a button the person has since left.
    if (!confirm.focus || applied.current === confirm) return;
    const target = { confirm: cancel, trigger, restore, archive }[confirm.focus].current;
    if (!target) return;
    target.focus();
    applied.current = confirm;
  }, [confirm, archived]);

  const set = (state: "active" | "archived" | "deleted") =>
    change.mutate(state, {
      onSuccess: () => {
        if (state === "deleted") {
          dispatch("done");
          router.push(`/documents?${DELETED_FLAG}=1`);
        } else {
          dispatch(state === "archived" ? "archived" : "restored");
        }
      },
      // A failed delete keeps the panel and puts focus back inside it, on the error.
      onError: () => {
        if (state === "deleted") dispatch("failed");
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
          <Button
            ref={restore}
            variant="outline"
            size="sm"
            disabled={change.isPending}
            onClick={() => set("active")}
          >
            <ArchiveRestore aria-hidden="true" />
            {t("restore")}
          </Button>
        ) : (
          <Button
            ref={archive}
            variant="outline"
            size="sm"
            disabled={change.isPending}
            onClick={() => set("archived")}
          >
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
          {change.isError ? (
            <p className="text-meta text-danger" role="alert">
              {t("actionFailed")}
            </p>
          ) : null}
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
      {change.isError && !confirm.open ? (
        <p className="text-meta text-danger" role="alert">
          {t("actionFailed")}
        </p>
      ) : null}
    </div>
  );
}
