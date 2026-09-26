"use client";

import Image from "next/image";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { RotateCw, WifiOff } from "lucide-react";
import { art, type ArtName } from "@/components/nora/art";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { useOnline } from "@/hooks/use-online";
import { Button } from "./button";

// The three ways a read can come back empty-handed, each said differently
// (R-27): nothing is there, it isn't there any more, or she couldn't check.
// "Couldn't check" is never shown as "nothing".

/** One sentence saying what would appear here and how it gets there
 * (DESIGN.md §7). Mini Nora may sit beside it, small. */
export function EmptyState({
  title,
  children,
  art: artName,
  action,
  className,
  compact = false,
}: {
  title?: ReactNode;
  children?: ReactNode;
  art?: ArtName;
  action?: ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 text-center animate-fade",
        compact ? "py-6" : "py-10 sm:py-14",
        className,
      )}
    >
      {artName ? (
        <Image
          src={art[artName]}
          alt=""
          sizes="96px"
          className={cn("h-auto", compact ? "w-16" : "w-20 sm:w-24")}
        />
      ) : null}
      <div className="grid max-w-[34ch] gap-1">
        {title ? <p className="text-title font-semibold text-ink">{title}</p> : null}
        {children ? <p className="text-body text-ink-soft">{children}</p> : null}
      </div>
      {action}
    </div>
  );
}

/** "I couldn't check": the upstream is down, or this device is offline. */
export function UnavailableState({
  what,
  onRetry,
  className,
  compact = false,
}: {
  what?: ReactNode;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}) {
  const t = useTranslations("app");
  const online = useOnline();
  return (
    <div
      role="status"
      className={cn(
        "flex flex-col items-center gap-3 text-center animate-fade",
        compact ? "py-6" : "py-10",
        className,
      )}
    >
      {online ? (
        !compact && <Image src={art.lost} alt="" sizes="96px" className="h-auto w-20" />
      ) : (
        <WifiOff className="size-6 text-ink-muted" aria-hidden="true" />
      )}
      <div className="grid max-w-[36ch] gap-1">
        <p className="text-title font-semibold text-ink">
          {online ? t("unavailable") : t("offline")}
        </p>
        <p className="text-body text-ink-soft">
          {online ? (what ?? t("unavailableDetail")) : t("offlineDetail")}
        </p>
      </div>
      {onRetry && online ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCw aria-hidden="true" />
          {t("retry")}
        </Button>
      ) : null}
    </div>
  );
}

export function describeError(error: unknown): "not-found" | "unavailable" {
  return error instanceof ApiError && error.kind === "not-found" ? "not-found" : "unavailable";
}
