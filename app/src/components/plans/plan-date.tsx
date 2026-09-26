"use client";

import { useLocale, useTranslations } from "next-intl";
import { dateFormat } from "@/lib/format";
import { cn } from "@/lib/cn";

/** A small calendar leaf: the weekday over the day of the month, in the
 * display zone. An undated plan shows a question instead of a guess. */
export function PlanDate({
  when,
  className,
  muted = false,
}: {
  when: string | null;
  className?: string;
  muted?: boolean;
}) {
  const locale = useLocale();
  const t = useTranslations("plans");
  const at = when ? Date.parse(when) : null;
  const fmt = (o: Intl.DateTimeFormatOptions) =>
    at === null ? "" : dateFormat(locale, o).format(at);
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid w-12 shrink-0 justify-items-center overflow-hidden rounded-control border border-hairline bg-studio text-center",
        muted && "opacity-60",
        className,
      )}
    >
      <span className="w-full bg-hoodie py-0.5 text-[0.6875rem] font-semibold text-ink-soft">
        {at === null ? t("tbdShort") : fmt({ weekday: "short" })}
      </span>
      <span className="py-1 font-mono text-[1.125rem] leading-tight font-medium text-ink tnum">
        {at === null ? "?" : fmt({ day: "numeric" }).replace(/[^\d]/g, "")}
      </span>
    </span>
  );
}
