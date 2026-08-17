"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { X, Sparkles } from "lucide-react";

// A real bottom sheet, not a TODO — the center nav button needs somewhere
// to land today even though the actual chat pipe into Nora doesn't exist on
// web yet (she only talks over Signal/Discord right now). No Radix Dialog
// dependency for one sheet — a fixed overlay + panel covers this.
export function ChatSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useTranslations("chat");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label={t("heading")}>
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={() => onOpenChange(false)}
      />
      <div className="absolute bottom-0 left-0 right-0 max-h-[80vh] rounded-t-2xl border-t border-border bg-bg-surface p-5 pb-[calc(env(safe-area-inset-bottom)+1.25rem)] shadow-lg">
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-border-strong" />

        <div className="mb-4 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-[17px] font-medium tracking-tight">{t("heading")}</h2>
            <p className="mt-0.5 text-[13px] text-text-secondary">{t("dek")}</p>
          </div>
          <button
            onClick={() => onOpenChange(false)}
            aria-label={t("close")}
            className="rounded-md p-1.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4 flex items-center gap-2 rounded-lg border border-accent/25 bg-accent/10 p-3 text-[12.5px] text-accent">
          <Sparkles className="h-4 w-4 shrink-0" />
          {t("comingSoon")}
        </div>

        <div className="flex items-center gap-2 opacity-50">
          <input
            disabled
            placeholder={t("inputPlaceholder")}
            className="flex-1 rounded-full border border-border bg-bg-elevated px-4 py-2.5 text-[13px] text-text-primary placeholder:text-text-muted"
          />
          <button
            disabled
            className="rounded-full bg-accent px-4 py-2.5 text-[13px] font-medium text-bg-base"
          >
            {t("send")}
          </button>
        </div>
      </div>
    </div>
  );
}
