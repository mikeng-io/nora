"use client";

import { useLocale } from "next-intl";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useNow } from "@/hooks/use-now";

/** A relative time that keeps moving ("5 minutes ago" becomes "6 minutes
 * ago"), with the absolute time on hover and for assistive tech. Renders
 * nothing time-relative on the server, so hydration never disagrees. */
export function Ago({
  ts,
  className,
  style = "long",
}: {
  ts: number;
  className?: string;
  style?: "long" | "narrow";
}) {
  const locale = useLocale();
  const now = useNow();
  const absolute = formatDateTime(ts, locale);
  return (
    <time dateTime={new Date(ts).toISOString()} title={absolute} className={className}>
      {now === 0 ? absolute : formatRelative(ts, locale, now, style)}
    </time>
  );
}
