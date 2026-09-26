"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { WifiOff } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { useOnline } from "@/hooks/use-online";

// Offline, the portal keeps showing what it last read, and says so. Coming
// back, it says that too and refreshes everything at once.
export function OfflineBanner() {
  const t = useTranslations("app");
  const online = useOnline();
  const client = useQueryClient();
  const was = useRef(online);

  // Offline, nothing on screen is current, so nothing may pulse as live
  // (DESIGN.md §6: live state loops only while it is live). globals.css
  // stills every live mark under this attribute.
  useEffect(() => {
    document.documentElement.toggleAttribute("data-offline", !online);
  }, [online]);

  useEffect(() => {
    if (!was.current && online) {
      toast.success(t("backOnline"));
      void client.invalidateQueries();
    }
    was.current = online;
  }, [online, client, t]);

  if (online) return null;
  return (
    <div
      role="status"
      className="sticky top-[calc(env(safe-area-inset-top)+56px)] z-30 flex items-center justify-center gap-2 border-b border-hairline bg-hoodie px-4 py-2 text-meta font-medium text-ink-soft animate-fade lg:top-0"
    >
      <WifiOff className="size-4 shrink-0" aria-hidden="true" />
      <span>{t("offlineBanner")}</span>
    </div>
  );
}
