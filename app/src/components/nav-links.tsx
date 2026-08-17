"use client";

import { useTranslations } from "next-intl";
import { Activity, Gamepad2, MapPin, Sparkles } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
  games: Gamepad2,
  map: MapPin,
};

export function NavLinks() {
  const pathname = usePathname();
  const t = useTranslations("nav");

  return (
    <nav className="flex flex-col gap-0.5">
      {MODULES.map((m) => {
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
