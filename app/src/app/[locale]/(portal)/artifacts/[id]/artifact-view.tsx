"use client";

import { useLocale, useTranslations } from "next-intl";
import { ChevronLeft, Download } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { isNotFound } from "@/lib/api/client";
import { artifactDownloadPath, type Artifact } from "@/lib/api/artifacts";
import { previewOf, typeLabel } from "@/lib/artifacts/list";
import { detailView, roomName } from "@/lib/documents/list";
import { formatBytes, formatDateTime } from "@/lib/format";
import { useArtifact } from "@/lib/queries";

// One file Nora sent, and its download. A link's page shows its newest file. Unreadable
// and unknown read the same here because Nora answers them the same: a link never
// confirms that a file exists. A raster picture, audio or video is shown in place; every
// other file, an SVG or a PDF included, is only offered as a download.
export function ArtifactPageView({ id }: { id: string }) {
  const t = useTranslations("artifacts");
  const item = useArtifact(id);
  const view = detailView({
    isPending: item.isPending,
    isError: item.isError,
    hasData: item.data !== undefined,
    notFound: isNotFound(item.error),
  });
  if (view === "loading") {
    return (
      <PageFrame>
        <Skeleton className="mb-3 h-10 w-2/3" />
        <Skeleton className="mb-8 h-5 w-1/2" />
        <Skeleton className="h-40 rounded-panel" />
      </PageFrame>
    );
  }
  if (view === "not-found" || view === "unavailable" || !item.data) {
    return (
      <PageFrame>
        {view === "unavailable" ? (
          <UnavailableState onRetry={() => item.refetch()} />
        ) : (
          <EmptyState
            art="lost"
            title={t("notFoundTitle")}
            action={
              <Link href="/artifacts" className="text-meta font-semibold text-denim hover:underline">
                {t("back")}
              </Link>
            }
          >
            {t("notFound")}
          </EmptyState>
        )}
      </PageFrame>
    );
  }
  return <Loaded artifact={item.data} />;
}

function Loaded({ artifact }: { artifact: Artifact }) {
  const t = useTranslations("artifacts");
  const locale = useLocale();
  useRegisterTitle(artifact.title);
  const href = artifactDownloadPath(artifact.id);
  const preview = previewOf(artifact.mediaType);
  const sent = formatDateTime(Date.parse(artifact.createdAt), locale);
  return (
    <PageFrame>
      <Link
        href="/artifacts"
        className="mb-4 inline-flex items-center gap-1 text-meta font-semibold text-ink-muted hover:text-ink"
      >
        <ChevronLeft className="size-4" aria-hidden="true" />
        {t("back")}
      </Link>
      <h1 className="mb-2 text-[1.5rem] leading-tight font-semibold break-words text-ink">{artifact.title}</h1>
      <p className="mb-6 text-meta text-ink-muted" data-testid="artifact-meta">
        {t(artifact.revised ? "metaRevised" : "meta", {
          type: typeLabel(artifact.mediaType),
          size: formatBytes(artifact.size),
          room: roomName(artifact),
          date: sent,
        })}
      </p>
      {preview === "image" ? (
        <img src={href} alt={artifact.title} className="mb-6 max-h-[70vh] w-auto max-w-full rounded-panel border border-hairline" />
      ) : preview === "audio" ? (
        <audio controls preload="metadata" src={href} className="mb-6 w-full" />
      ) : preview === "video" ? (
        <video controls preload="metadata" src={href} className="mb-6 max-h-[70vh] w-full rounded-panel" />
      ) : null}
      <a
        href={href}
        download
        className="inline-flex h-10 items-center gap-2 rounded-control bg-ink px-4 text-[0.875rem] font-semibold text-studio hover:opacity-90"
      >
        <Download className="size-4" aria-hidden="true" />
        {t("download")}
      </a>
    </PageFrame>
  );
}
