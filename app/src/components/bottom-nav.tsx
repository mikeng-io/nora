"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Activity, Gamepad2, MapPin, Sparkles, MessageCircle } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";
import { ChatSheet } from "@/components/chat-sheet";

const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
  games: Gamepad2,
  map: MapPin,
};

// Mobile-only (md:hidden — the sidebar in (portal)/layout.tsx owns nav on
// desktop). Matches trustless/apps/web's BottomNav.tsx structure exactly:
// h-20, shadow-sm + backdrop-blur-2xl, the gradient hairline along the top
// edge, 44px+ touch targets, a raised center action button (-mt-10, h-14
// w-14, rounded-xl, bg-accent) that opens a sheet — trustless's opens an
// "Operations Sheet"; Nora's opens a chat-with-Nora sheet instead, since
// that's the equivalent thing to reach for from anywhere in her portal.
export function BottomNav() {
  const pathname = usePathname();
  const t = useTranslations("nav");
  const [chatOpen, setChatOpen] = useState(false);

  const left = MODULES.slice(0, 2);
  const right = MODULES.slice(2);

  const renderLink = (m: (typeof MODULES)[number]) => {
    const Icon = ICONS[m.id];
    const active = pathname === m.href || pathname.startsWith(`${m.href}/`);
    return (
      <Link
        key={m.id}
        href={m.href}
        className={cn(
          "flex min-h-[44px] min-w-12 flex-col items-center justify-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-text-secondary transition-colors duration-150",
          active
            ? "border-accent/25 bg-accent/10 text-accent"
            : "hover:bg-bg-elevated hover:text-text-primary",
        )}
      >
        <Icon className="h-5 w-5" />
        <span className="text-[8px]">{t(m.labelKey)}</span>
      </Link>
    );
  };

  return (
    <>
      <nav className="fixed bottom-0 left-0 right-0 z-50 flex h-20 items-center justify-between border-t border-border bg-bg-surface/95 px-4 pb-[env(safe-area-inset-bottom)] shadow-sm backdrop-blur-2xl md:hidden">
        <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/30 to-transparent" />

        <div className="flex h-full flex-1 items-center justify-around">
          {left.map(renderLink)}
        </div>

        <div className="relative z-50 -mt-10 flex flex-col items-center justify-center px-4">
          <button
            onClick={() => setChatOpen(true)}
            aria-label={t("chat")}
            className="group relative flex h-14 w-14 items-center justify-center rounded-xl border border-accent/35 bg-accent text-bg-base outline-none transition-colors hover:bg-accent-dim focus-visible:ring-2 focus-visible:ring-accent/70 active:scale-95"
          >
            <MessageCircle className="h-6 w-6" />
          </button>
          <span className="mt-2 text-[9px] text-accent">{t("chat")}</span>
        </div>

        <div className="flex h-full flex-1 items-center justify-around">
          {right.map(renderLink)}
        </div>
      </nav>

      <ChatSheet open={chatOpen} onOpenChange={setChatOpen} />
    </>
  );
}
