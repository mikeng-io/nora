"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Sparkles } from "lucide-react";
import { MODULES } from "@/lib/modules";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof Activity> = {
  status: Activity,
  memory: Sparkles,
};

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-0.5">
      {MODULES.map((m) => {
        const Icon = ICONS[m.id];
        const active = pathname === m.href;
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
            {m.label}
          </Link>
        );
      })}
    </nav>
  );
}
