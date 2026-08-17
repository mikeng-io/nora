"use client";

import { useTranslations } from "next-intl";
import { Activity, Gamepad2, Sparkles } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
  games: Gamepad2,
};

// Mobile-only (md:hidden — the sidebar in (portal)/layout.tsx owns nav on
// desktop). Matches trustless/apps/web's BottomNav.tsx exactly: h-20,
// shadow-sm + backdrop-blur-2xl, the gradient hairline along the top edge,
// 44px+ touch targets. The one thing deliberately NOT ported is their
// floating center action button — that's tied to a "quick ops" sheet
// feature Nora has no equivalent of; three flat, equal tabs is what her
// actual nav is, not a place to invent a fourth feature to fill a shape.
export function BottomNav() {
  const pathname = usePathname();
  const t = useTranslations("nav");

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 flex h-20 items-center justify-around border-t border-border bg-bg-surface/95 px-4 pb-[env(safe-area-inset-bottom)] shadow-sm backdrop-blur-2xl md:hidden">
      <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/30 to-transparent" />

      {MODULES.map((m) => {
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
      })}
    </nav>
  );
}
