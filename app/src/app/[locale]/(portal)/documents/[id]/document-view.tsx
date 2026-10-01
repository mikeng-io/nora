"use client";

import { useState } from "react";
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
import { platformLabel, isRevised } from "@/lib/documents/list";
import { formatRelative } from "@/lib/format";
import { useDocument, useSetDocumentState } from "@/lib/queries";
import { useNow } from "@/hooks/use-now";

// One document, drawn from its stored spec by the same registry the fixtures use, in
// the portal's own theme. Unreadable and unknown read the same here because Nora
// answers them the same: an id never confirms that a document exists.
export function DocumentPageView({ id }: { id: string }) {
  const t = useTranslations("documents");
  const doc = useDocument(id);

  if (doc.isPending) {
    return (
      <PageFrame width="narrow">
        <Skeleton className="mb-3 h-10 w-2/3" />
        <Skeleton className="mb-8 h-5 w-1/2" />
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  if (doc.isError && !doc.data) {
    return (
      <PageFrame width="narrow">
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
  return <Loaded doc={doc.data} />;
}

function Loaded({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents");
  const locale = useLocale();
  const now = useNow() || Date.now();
  // The spec's own Page draws the one <h1>; this only tells the phone's top bar the title.
  useRegisterTitle(doc.title);
  const ago = formatRelative(Date.parse(doc.updatedAt), locale, now);
  const who = { name: doc.createdBy.name, platform: platformLabel(doc.platform) };
  return (
    <PageFrame width="narrow">
      <Link
        href="/documents"
        className="-ml-1.5 mb-4 inline-flex h-8 w-fit items-center gap-0.5 rounded-control pr-2 pl-0.5 text-meta font-medium text-ink-muted transition-colors duration-100 hover:bg-hoodie hover:text-ink"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        {t("back")}
      </Link>
      <p className="mb-3 text-meta text-ink-muted" data-testid="document-meta">
        {isRevised(doc.revision)
          ? t("madeRevised", { ...who, n: doc.revision, ago })
          : t("made", who)}
      </p>
      <Lifecycle doc={doc} />
      <DocumentView doc={doc.document} documentId={doc.id} />
    </PageFrame>
  );
}

// Archive, delete (after a confirm) or restore. Each one is recorded by Nora before the
// page changes: nothing here is optimistic. A delete leaves the page, because the
// document no longer opens for anyone; it is restored by asking Nora in the chat.
function Lifecycle({ doc }: { doc: DocumentDetail }) {
  const t = useTranslations("documents");
  const router = useRouter();
  const change = useSetDocumentState(doc.id);
  const [confirming, setConfirming] = useState(false);
  const archived = doc.state === "archived";

  const set = (state: "active" | "archived" | "deleted") =>
    change.mutate(state, {
      onSuccess: () => {
        setConfirming(false);
        if (state === "deleted") router.push("/documents");
      },
    });

  return (
    <div className="mb-6 grid gap-3" data-testid="document-lifecycle">
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
        {confirming ? null : (
          <Button
            variant="destructive"
            size="sm"
            disabled={change.isPending}
            onClick={() => setConfirming(true)}
          >
            <Trash2 aria-hidden="true" />
            {t("delete")}
          </Button>
        )}
      </div>
      {confirming ? (
        <div
          role="alertdialog"
          aria-labelledby="delete-title"
          aria-describedby="delete-body"
          className="grid gap-3 rounded-panel border border-hairline bg-surface p-4"
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
            <Button variant="quiet" size="sm" disabled={change.isPending} onClick={() => setConfirming(false)}>
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
