"use client";

import { useState } from "react";
import { Drawer } from "vaul";
import { Ellipsis } from "lucide-react";
import { useTranslations } from "next-intl";
import { Link, usePathname } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { BOTTOM_NAV_COLUMNS, MAIN_NAV, MOBILE_NAV_MAX, NORA_NAV, isActive, type NavItem } from "./nav";
import { NavIcon } from "./nav-icon";

// Phone navigation: a bottom bar on surface holding the everyday places plus a
// More cell, which opens a sheet with everywhere else — the rest of the circle
// and the pages about her. At most MOBILE_NAV_MAX cells, no raised centre
// button (DESIGN.md §7).
export function BottomNav() {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const items = MAIN_NAV.filter((i) => i.mobile);
  const overflow = [...MAIN_NAV.filter((i) => !i.mobile), ...NORA_NAV];
  const moreActive = overflow.some((i) => isActive(pathname, i));

  const row = (it: NavItem) => {
    const active = isActive(pathname, it);
    return (
      <li key={it.key}>
        <Link
          href={it.href}
          aria-current={active ? "page" : undefined}
          onClick={() => setOpen(false)}
          className={cn(
            "flex h-12 items-center gap-3 rounded-control px-1 text-[0.9375rem] font-medium active:bg-hoodie",
            active ? "text-denim" : "text-ink",
          )}
        >
          <span className="grid w-6 place-items-center text-ink-muted">
            <NavIcon item={it} active={active} size={20} />
          </span>
          {t(it.key)}
        </Link>
      </li>
    );
  };

  return (
    <nav
      aria-label={t("main")}
      className="fixed inset-x-0 bottom-0 z-40 border-t border-hairline bg-surface/92 backdrop-blur-xl backdrop-saturate-150 lg:hidden"
    >
      <ul
        className={cn(
          "mx-auto grid max-w-xl pb-[env(safe-area-inset-bottom)]",
          BOTTOM_NAV_COLUMNS[Math.min(items.length + 1, MOBILE_NAV_MAX)],
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
        <li>
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-current={moreActive ? "page" : undefined}
            className={cn(
              "flex h-[60px] w-full flex-col items-center justify-center gap-1 text-[0.6875rem] font-semibold transition-colors duration-100",
              moreActive ? "text-denim" : "text-ink-muted active:text-ink",
            )}
          >
            <span
              className={cn(
                "grid h-7 w-14 place-items-center rounded-full transition-colors duration-160 ease-nora",
                moreActive && "bg-hoodie",
              )}
            >
              <Ellipsis aria-hidden="true" strokeWidth={moreActive ? 2.1 : 1.8} className="size-[21px]" />
            </span>
            {t("more")}
          </button>
        </li>
      </ul>

      <Drawer.Root open={open} onOpenChange={setOpen}>
        <Drawer.Portal>
          <Drawer.Overlay className="fixed inset-0 z-50 bg-(--scrim)" />
          <Drawer.Content
            className="fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col rounded-t-sheet border-t border-hairline bg-surface outline-none"
            aria-describedby={undefined}
          >
            <Drawer.Title className="sr-only">{t("more")}</Drawer.Title>
            <div className="mx-auto mt-2.5 mb-1 h-1 w-10 shrink-0 rounded-full bg-seam" aria-hidden="true" />
            <div className="scroll-thin overflow-y-auto px-4 pb-[calc(env(safe-area-inset-bottom)+16px)]">
              <ul className="grid py-2">{MAIN_NAV.filter((i) => !i.mobile).map(row)}</ul>
              <p className="border-t border-hairline px-1 pt-3 pb-1 text-label font-medium text-ink-muted">
                {t("aboutNora")}
              </p>
              <ul className="grid pb-2">{NORA_NAV.map(row)}</ul>
            </div>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    </nav>
  );
}
