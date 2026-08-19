"use client";

import { useTranslations } from "next-intl";
import { Activity, BookOpen, CalendarDays, Gamepad2, MapPin, Sparkles, Users } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";

// Every MODULES entry needs a key here, live or not — a module flipping
// back to "live" must never re-expose the missing-icon crash that shipped
// once already. src/components/nav-icons.test.ts asserts this map (and
// bottom-nav.tsx's) stays in sync with MODULES.
export const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
  games: Gamepad2,
  map: MapPin,
  people: Users,
  events: CalendarDays,
  doctrine: BookOpen,
};

export function NavLinks() {
  const pathname = usePathname();
  const t = useTranslations("nav");

  return (
    <nav className="flex flex-col gap-0.5">
      {MODULES.filter((m) => m.status === "live").map((m) => {
        const Icon = ICONS[m.id];
        const active = pathname === m.href || pathname.startsWith(`${m.href}/`);
        return (
          <Link
            key={m.id}
            href={m.href}
            className={cn(
              "flex items-center gap-2 rounded-md border border-transparent px-2.5 py-2 text-sm transition-colors",
              active
                ? "border-border-strong bg-bg-elevated text-text-primary"
                : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary",
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {t(m.labelKey)}
          </Link>
        );
      })}
    </nav>
  );
}
