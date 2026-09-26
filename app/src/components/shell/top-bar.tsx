"use client";

import { useTranslations } from "next-intl";
import { Search } from "lucide-react";
import { Link } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { NoraMark, PersonAvatar, Wordmark } from "@/components/ui/avatar";
import { AccountSheet } from "./account-menu";
import { useCommandPalette } from "./command-palette";
import { NoraPresenceDot } from "./nora-presence";
import { useCompactTitle } from "./page-title";
import { useViewer } from "./viewer";

// Phone and tablet: her mark on the left (it opens her own page), the
// page's title once its heading scrolls away, search and the viewer on the
// right.
export function TopBar() {
  const t = useTranslations("nav");
  const viewer = useViewer();
  const palette = useCommandPalette();
  const { title, visible } = useCompactTitle();

  return (
    <header className="safe-top sticky top-0 z-40 border-b border-hairline bg-surface/90 backdrop-blur-xl backdrop-saturate-150 lg:hidden">
      <div className="relative flex h-14 items-center gap-1 px-2">
        <Link
          href="/nora"
          aria-label={t("nora")}
          className="flex h-11 items-center gap-1.5 rounded-full pr-3 pl-1.5 active:bg-hoodie"
        >
          <NoraMark size={28} />
          <Wordmark
            className={cn(
              "text-[1.1875rem] transition-opacity duration-160 ease-nora",
              visible && title ? "opacity-0" : "opacity-100",
            )}
          />
          <NoraPresenceDot className="mt-0.5" />
        </Link>

        <p
          aria-hidden={!visible}
          className={cn(
            "pointer-events-none absolute inset-x-24 truncate text-center text-[0.9375rem] font-semibold text-ink transition-[opacity,transform] duration-160 ease-nora",
            visible && title ? "translate-y-0 opacity-100" : "translate-y-1 opacity-0",
          )}
        >
          {title}
        </p>

        <div className="ml-auto flex items-center">
          <button
            type="button"
            onClick={palette.open}
            aria-label={t("search")}
            className="grid size-11 place-items-center rounded-full text-ink-soft active:bg-hoodie"
          >
            <Search className="size-5" aria-hidden="true" />
          </button>
          <AccountSheet
            trigger={
              <button
                type="button"
                aria-label={t("account")}
                className="grid size-11 place-items-center rounded-full active:bg-hoodie"
              >
                <PersonAvatar id={viewer.personId} name={viewer.displayName} size={30} />
              </button>
            }
          />
        </div>
      </div>
    </header>
  );
}
