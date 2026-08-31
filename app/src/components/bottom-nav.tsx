"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Activity, BookOpen, CalendarDays, Gamepad2, MapPin, Sparkles, Users, Wrench, Menu } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";
import { NavSheet } from "@/components/nav-sheet";

// Every MODULES entry needs a key here, live or not — a module flipping
// back to "live" must never re-expose the missing-icon crash that shipped
// once already. src/components/nav-icons.test.ts asserts this map (and
// nav-links.tsx's) stays in sync with MODULES.
export const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
  games: Gamepad2,
  map: MapPin,
  people: Users,
  events: CalendarDays,
  doctrine: BookOpen,
  tools: Wrench,
};

// Mobile-only (md:hidden — the sidebar in (portal)/layout.tsx owns nav on
// desktop). Expandable structure: the bar keeps only the FIRST TWO live
// modules left and the LAST TWO right, with a center Menu button that opens
// a bottom sheet listing EVERY live module — so adding a ninth module never
// crowds the bar again (the pre-sheet bar was squeezing 8 items into 44px
// targets and labels were dropping to 8px). The chat button is retired: the
// chat pipe into Nora doesn't exist on web yet, and the placeholder sheet
// taught nothing — Menu is the honest center action.
export function BottomNav() {
  const pathname = usePathname();
  const t = useTranslations("nav");
  const [menuOpen, setMenuOpen] = useState(false);

  const liveModules = MODULES.filter((m) => m.status === "live");
  const left = liveModules.slice(0, 2);
  const right = liveModules.slice(Math.max(2, liveModules.length - 2));

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
        <span className="text-[10px]">{t(m.labelKey)}</span>
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
            onClick={() => setMenuOpen(true)}
            aria-label={t("menu")}
            className="group relative flex h-14 w-14 items-center justify-center rounded-full border border-accent/35 bg-accent text-bg-base outline-none transition-colors hover:bg-accent-dim focus-visible:ring-2 focus-visible:ring-accent/70 active:scale-95"
          >
            <Menu className="h-6 w-6" />
          </button>
          <span className="mt-2 text-[9px] text-accent">{t("menu")}</span>
        </div>

        <div className="flex h-full flex-1 items-center justify-around">
          {right.map(renderLink)}
        </div>
      </nav>

      <NavSheet open={menuOpen} onOpenChange={setMenuOpen} />
    </>
  );
}
