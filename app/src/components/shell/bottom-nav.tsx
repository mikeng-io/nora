"use client";

import { useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { BOTTOM_NAV_COLUMNS, MAIN_NAV, MOBILE_NAV_MAX, isActive } from "./nav";
import { NavIcon } from "./nav-icon";

// Phone navigation: a bottom bar on surface, up to MOBILE_NAV_MAX destinations, no
// raised centre button (DESIGN.md §7).
export function BottomNav() {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const items = MAIN_NAV.filter((i) => i.mobile);
  return (
    <nav
      aria-label={t("main")}
      className="fixed inset-x-0 bottom-0 z-40 border-t border-hairline bg-surface/92 backdrop-blur-xl backdrop-saturate-150 lg:hidden"
    >
      <ul
        className={cn(
          "mx-auto grid max-w-xl pb-[env(safe-area-inset-bottom)]",
          BOTTOM_NAV_COLUMNS[Math.min(items.length, MOBILE_NAV_MAX)],
        )}
      >
        {items.map((it) => {
          const active = isActive(pathname, it);
          return (
            <li key={it.key}>
              <Link
                href={it.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-[60px] flex-col items-center justify-center gap-1 text-[0.6875rem] font-semibold transition-colors duration-100",
                  active ? "text-denim" : "text-ink-muted active:text-ink",
                )}
              >
                <span
                  className={cn(
                    "grid h-7 w-14 place-items-center rounded-full transition-colors duration-160 ease-nora",
                    active && "bg-hoodie",
                  )}
                >
                  <NavIcon item={it} active={active} size={21} />
                </span>
                {t(it.key)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
