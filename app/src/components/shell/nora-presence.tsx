"use client";

import { useTranslations } from "next-intl";
import { useStatus } from "@/lib/queries";
import { cn } from "@/lib/cn";

export type NoraState = "listening" | "degraded" | "unreachable" | "unknown";

/** Whether she is up: her record and her memory both answer. Anything less
 * is said, not hidden (the status page gives the detail). */
export function useNoraState(): NoraState {
  const { data, isError, isPending } = useStatus();
  if (isPending) return "unknown";
  if (isError || !data) return "unreachable";
  return data.store.reachable && data.honcho.reachable ? "listening" : "degraded";
}

/** Her presence dot: blush while she is listening (the Blush Rule: this dot
 * is her), a hollow seam ring otherwise. */
export function NoraPresenceDot({ className }: { className?: string }) {
  const state = useNoraState();
  const t = useTranslations("nora.presence");
  return (
    <span
      role="img"
      aria-label={t(state)}
      title={t(state)}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        state === "listening" ? "bg-blush" : "border-[1.5px] border-seam",
        className,
      )}
    />
  );
}
