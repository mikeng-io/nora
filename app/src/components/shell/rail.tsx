"use client";

import { useTranslations } from "next-intl";
import { Search } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { NoraMark, Wordmark } from "@/components/ui/avatar";
import { AccountMenu } from "./account-menu";
import { MAIN_NAV, NORA_NAV, isActive, type NavItem } from "./nav";
import { NavIcon } from "./nav-icon";
import { NoraPresenceDot } from "./nora-presence";
import { useCommandPalette } from "./command-palette";

// Desktop navigation (DESIGN.md §7): a left rail on surface with a hairline
// edge, icon and sentence-case label, the active item in denim on hoodie.
export function Rail() {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const palette = useCommandPalette();

  const item = (it: NavItem) => {
    const active = isActive(pathname, it);
    return (
      <li key={it.key}>
        <Link
          href={it.href}
          aria-current={active ? "page" : undefined}
          className={cn(
            "flex h-10 items-center gap-3 rounded-control px-2.5 text-[0.9375rem] font-medium transition-colors duration-100 ease-nora",
            active ? "bg-hoodie text-denim" : "text-ink-soft hover:bg-hoodie/70 hover:text-ink",
          )}
        >
          <span className="grid w-6 place-items-center">
            <NavIcon item={it} active={active} />
          </span>
          {t(it.key)}
        </Link>
      </li>
    );
  };

  return (
    <aside className="sticky top-0 hidden h-dvh w-[248px] shrink-0 flex-col border-r border-hairline bg-surface lg:flex">
      <div className="flex h-16 items-center gap-2 px-4">
        <Link
          href="/today"
          className="flex items-center gap-2 rounded-full pr-2"
          aria-label={t("home")}
        >
          <NoraMark size={30} />
          <Wordmark />
        </Link>
        <NoraPresenceDot className="mt-1" />
      </div>

      <div className="px-3 pb-3">
        <button
          type="button"
          onClick={palette.open}
          className="flex h-10 w-full items-center gap-2.5 rounded-control border border-hairline bg-studio px-2.5 text-left text-meta text-ink-muted transition-colors duration-100 hover:border-seam hover:text-ink-soft"
        >
          <Search className="size-4" aria-hidden="true" />
          <span className="flex-1">{t("search")}</span>
          <kbd className="rounded-md border border-hairline bg-surface px-1.5 font-mono text-[0.6875rem] text-ink-muted">
            ⌘K
          </kbd>
        </button>
      </div>

      <nav aria-label={t("main")} className="scroll-thin flex-1 overflow-y-auto px-3">
        <ul className="grid gap-0.5">{MAIN_NAV.map(item)}</ul>
        <p className="mt-6 mb-1.5 px-2.5 text-label font-medium text-ink-muted">{t("aboutNora")}</p>
        <ul className="grid gap-0.5">{NORA_NAV.map(item)}</ul>
      </nav>

      <div className="border-t border-hairline p-2">
        <AccountMenu />
      </div>
    </aside>
  );
}
