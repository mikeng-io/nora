"use client";

import { useLocale, useTranslations } from "next-intl";
import { CloudOff } from "lucide-react";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/cn";

/** When a refresh failed and the last answer is still on screen, say so and
 * say how old it is: an old position shown without its time reads as now. */
export function RefreshNote({
  query,
  className,
}: {
  query: { isError: boolean; data: unknown; dataUpdatedAt: number };
  className?: string;
}) {
  const t = useTranslations("app");
  const locale = useLocale();
  if (!query.isError || query.data === undefined || !query.dataUpdatedAt) return null;
  return (
    <span role="status" className={cn("inline-flex items-center gap-1.5 text-meta text-caution", className)}>
      <CloudOff className="size-3.5 shrink-0" aria-hidden="true" />
      {t("asOf", { time: formatTime(query.dataUpdatedAt, locale) })}
    </span>
  );
}
