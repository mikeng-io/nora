"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BottomNav } from "./bottom-nav";
import { CommandPaletteProvider } from "./command-palette";
import { OfflineBanner } from "./offline-banner";
import { PageTitleProvider } from "./page-title";
import { Rail } from "./rail";
import { TopBar } from "./top-bar";

export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations("app");
  return (
    <TooltipProvider>
      <PageTitleProvider>
        <CommandPaletteProvider>
          <a
            href="#main"
            className="fixed top-3 left-3 z-[100] -translate-y-[200%] rounded-control bg-ink px-4 py-2.5 font-semibold text-studio transition-transform duration-160 focus-visible:translate-y-0"
          >
            {t("skip")}
          </a>
          <div className="flex min-h-dvh">
            <Rail />
            <div className="flex min-w-0 flex-1 flex-col">
              <TopBar />
              <OfflineBanner />
              <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
                {children}
              </main>
            </div>
          </div>
          <BottomNav />
        </CommandPaletteProvider>
      </PageTitleProvider>
    </TooltipProvider>
  );
}

/** The ordinary page frame: a readable column with room for the bottom bar. */
export function PageFrame({
  children,
  width = "default",
  className = "",
}: {
  children: ReactNode;
  width?: "default" | "wide" | "narrow";
  className?: string;
}) {
  const max = { default: "max-w-[1080px]", wide: "max-w-[1320px]", narrow: "max-w-[760px]" }[width];
  return (
    <div className={`page-enter mx-auto w-full ${max} px-4 pt-5 pb-nav sm:px-6 lg:px-10 lg:pt-10 ${className}`}>
      {children}
    </div>
  );
}
