"use client";

import { useState, type FormEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  CircleAlert,
  Download,
  FileAudio,
  FileVideo,
  File as FileIcon,
  Loader2,
  RotateCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Panel, PanelBody, PanelHeader, Rows } from "@/components/ui/panel";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import {
  downloadContentPath,
  downloadThumbnailPath,
  type DownloadJob,
  type DownloadStatus,
} from "@/lib/api/downloads";
import { formatBytes, formatRate, formatRelative } from "@/lib/format";
import { useConvertDownload, useDeleteDownload, useDownloads, useRetryDownload, useSubmitDownloads } from "@/lib/queries";
import { cn } from "@/lib/cn";
import { useNow } from "@/hooks/use-now";

// The circle's media shelf: paste URLs (one per line), yt-dlp fetches them on the
// server, and the finished files play or download from here. Everyone signed in
// sees the same library; a job says who asked for it.
export function DownloadsView() {
  const t = useTranslations("downloads");
  const locale = useLocale();
  const now = useNow() || Date.now();
  const [text, setText] = useState("");
  const list = useDownloads();
  const submit = useSubmitDownloads();
  const retry = useRetryDownload();
  const remove = useDeleteDownload();
  const convert = useConvertDownload();

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const urls = text.split(/\s+/).filter(Boolean);
    if (!urls.length || submit.isPending) return;
    submit.mutate(urls, {
      onSuccess: (result) => {
        if (result.jobs.length) setText("");
        if (result.rejected.length) {
          toast.error(t("rejectedToast", { count: result.rejected.length }));
        }
      },
      onError: () => toast.error(t("submitFailed")),
    });
  };

  return (
    <PageFrame>
      <PageHeader title={t("title")} description={t("lede")} />

      <Panel className="mb-4">
        <PanelBody className="pt-4">
          <form onSubmit={onSubmit} className="grid gap-3">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label={t("urlsLabel")}
              placeholder={t("urlsPlaceholder")}
              rows={3}
              spellCheck={false}
              autoComplete="off"
              className="w-full resize-y rounded-control border border-hairline bg-surface px-3 py-2 font-mono text-[0.8125rem] text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-denim"
            />
            <div className="flex items-center justify-between gap-3">
              <p className="text-meta text-ink-muted">{t("urlsHint")}</p>
              <Button type="submit" variant="primary" size="sm" disabled={!text.trim() || submit.isPending}>
                {submit.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Download aria-hidden="true" />
                )}
                {t("submit")}
              </Button>
            </div>
          </form>
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader
          title={t("library")}
          description={
            list.data
              ? `${t("used", { size: formatBytes(list.data.usedBytes) })} · ${t("ttlHint")}`
              : t("ttlHint")
          }
        />
        {list.isPending ? (
          <div className="grid gap-3 px-4 pb-4 sm:px-5 sm:pb-5" aria-label={t("loadingList")}>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : list.isError ? (
          <UnavailableState what={t("unavailable")} onRetry={() => list.refetch()} compact />
        ) : list.data.jobs.length === 0 ? (
          <EmptyState title={t("emptyTitle")} compact>
            {t("empty")}
          </EmptyState>
        ) : (
          <Rows>
            {list.data.jobs.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                locale={locale}
                now={now}
                t={t}
                onRetry={() => retry.mutate(job.id)}
                onDelete={() => remove.mutate(job.id)}
                onConvert={(target) => convert.mutate({ id: job.id, target })}
                busy={retry.isPending || remove.isPending || convert.isPending}
              />
            ))}
          </Rows>
        )}
      </Panel>
    </PageFrame>
  );
}

const STATUS_TONE: Record<DownloadStatus, "neutral" | "denim" | "success" | "danger"> = {
  queued: "neutral",
  probing: "denim",
  downloading: "denim",
  done: "success",
  failed: "danger",
};

function mediaIcon(job: DownloadJob) {
  if (job.mediaType?.startsWith("video/")) return FileVideo;
  if (job.mediaType?.startsWith("audio/")) return FileAudio;
  return FileIcon;
}

function JobRow({
  job,
  locale,
  now,
  t,
  onRetry,
  onDelete,
  onConvert,
  busy,
}: {
  job: DownloadJob;
  locale: string;
  now: number;
  t: ReturnType<typeof useTranslations>;
  onRetry: () => void;
  onDelete: () => void;
  onConvert: (target: string) => void;
  busy: boolean;
}) {
  const Icon = mediaIcon(job);
  const name = job.title ?? job.url;
  const active = job.status === "downloading" || job.status === "probing";
  const created = Date.parse(job.createdAt);

  return (
    <li className="flex items-start gap-3 px-4 py-3 sm:px-5">
      {job.status === "done" && job.hasThumb ? (
        // eslint-disable-next-line @next/next/no-img-element -- a job's own stored thumbnail
        <img
          src={downloadThumbnailPath(job.id)}
          alt=""
          className="mt-0.5 size-10 shrink-0 rounded-control border border-hairline object-cover"
          loading="lazy"
        />
      ) : (
        <Icon className="mt-0.5 size-5 shrink-0 text-ink-muted" aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {job.status === "done" ? (
            <a
              href={downloadContentPath(job.id)}
              className="truncate text-body font-medium text-ink underline-offset-2 hover:underline"
            >
              {name}
            </a>
          ) : (
            <span className="truncate text-body font-medium text-ink">{name}</span>
          )}
          <Badge tone={STATUS_TONE[job.status]}>{t(`status.${job.status}`)}</Badge>
          {job.extractor ? <Badge tone="outline">{job.extractor}</Badge> : null}
        </div>
        <p className="mt-0.5 truncate text-meta text-ink-muted">
          {job.url}
          {" · "}
          {Number.isNaN(created) ? "" : formatRelative(created, locale, now)}
          {job.fileSize != null ? ` · ${formatBytes(job.fileSize)}` : ""}
        </p>
        {job.status === "downloading" ? (
          <div className="mt-2">
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(job.progress.percent ?? 0)}
              className="h-1.5 w-full overflow-hidden rounded-full bg-hoodie"
            >
              <div
                className={cn("h-full rounded-full bg-denim transition-[width] duration-300")}
                style={{ width: `${job.progress.percent ?? 0}%` }}
              />
            </div>
            <p className="mt-1 text-meta text-ink-muted">
              {job.progress.percent != null ? `${Math.round(job.progress.percent)}%` : ""}
              {job.progress.totalBytes != null ? ` · ${formatBytes(job.progress.totalBytes)}` : ""}
              {job.progress.speedBps != null ? ` · ${formatRate(job.progress.speedBps)}` : ""}
            </p>
          </div>
        ) : null}
        {job.status === "done" && job.files && job.files.length > 1 ? (
          <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-meta">
            {job.files
              .filter((f) => f !== job.fileName)
              .map((f) => (
                <a
                  key={f}
                  href={downloadContentPath(job.id, f)}
                  download
                  className="text-denim underline-offset-2 hover:underline"
                >
                  {f}
                </a>
              ))}
          </p>
        ) : null}
        {job.status === "failed" && job.error ? (
          <p className="mt-1 flex items-start gap-1 text-meta text-danger">
            <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span className="break-all">{job.error}</span>
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {job.status === "done" && job.convertTargets && job.convertTargets.length > 0 ? (
          job.converting ? (
            <Loader2 className="size-4 animate-spin text-ink-muted" aria-label={t("converting")} />
          ) : (
            <select
              aria-label={t("convertLabel")}
              defaultValue=""
              disabled={busy}
              onChange={(e) => {
                if (e.target.value) onConvert(e.target.value);
                e.target.value = "";
              }}
              className="rounded-control border border-hairline bg-surface px-1.5 py-1 text-meta text-ink-muted"
            >
              <option value="" disabled>
                {t("convert")}
              </option>
              {job.convertTargets.map((tgt) => (
                <option key={tgt} value={tgt}>
                  {tgt}
                </option>
              ))}
            </select>
          )
        ) : null}
        {job.status === "done" ? (
          <a
            href={downloadContentPath(job.id)}
            download
            aria-label={t("download")}
            className={buttonClass("quiet", "icon-sm")}
          >
            <Download aria-hidden="true" />
          </a>
        ) : null}
        {job.status === "failed" ? (
          <Button variant="quiet" size="icon-sm" onClick={onRetry} disabled={busy} aria-label={t("retry")}>
            <RotateCw aria-hidden="true" />
          </Button>
        ) : null}
        <Button
          variant="quiet"
          size="icon-sm"
          onClick={onDelete}
          disabled={busy}
          aria-label={t("delete")}
          className="text-ink-muted hover:text-danger"
        >
          <Trash2 aria-hidden="true" />
        </Button>
      </div>
    </li>
  );
}
