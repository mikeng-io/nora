"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { ChevronRight, X } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { ICONS as NAV_ICONS } from "@/components/nav-links";
import { cn } from "@/lib/utils";

// The expandable mobile nav — a bottom sheet listing every live module as a
// full-width row. The bottom bar keeps only the essentials (2 left + menu
// center + 2 right); everything else lives here, one tap away. Same
// fixed-overlay + panel pattern as the retired ChatSheet (which was an
// un-wired placeholder — the chat pipe into Nora doesn't exist on web yet).
// No Radix Dialog dependency for one sheet.
export function NavSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const pathname = usePathname();
  const t = useTranslations("nav");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;

  const liveModules = MODULES.filter((m) => m.status === "live");

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label={t("menu")}>
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={() => onOpenChange(false)}
      />
      <div className="absolute bottom-0 left-0 right-0 max-h-[80vh] overflow-y-auto rounded-t-2xl border-t border-border bg-bg-surface p-5 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] shadow-lg">
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-border-strong" />

        <div className="mb-4 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-[17px] font-medium tracking-tight">{t("menu")}</h2>
            <p className="mt-0.5 text-[13px] text-text-secondary">{t("menuDek")}</p>
          </div>
          <button
            onClick={() => onOpenChange(false)}
            aria-label={t("close")}
            className="rounded-md p-1.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex flex-col gap-1">
          {liveModules.map((m) => {
            const Icon = NAV_ICONS[m.id];
            const active = pathname === m.href || pathname.startsWith(`${m.href}/`);
            return (
              <Link
                key={m.id}
                href={m.href}
                onClick={() => onOpenChange(false)}
                className={cn(
                  "flex min-h-[52px] items-center justify-between rounded-lg border border-transparent px-3 py-2.5 text-[14px] transition-colors",
                  active
                    ? "border-accent/25 bg-accent/10 text-accent"
                    : "text-text-primary hover:bg-bg-elevated",
                )}
              >
                <span className="flex items-center gap-3">
                  <Icon className="h-4.5 w-4.5 h-[18px] w-[18px]" />
                  {t(m.labelKey)}
                </span>
                <ChevronRight className="h-4 w-4 text-text-muted" />
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
