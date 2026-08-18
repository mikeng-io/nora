import { redirect } from "next/navigation";
import * as Separator from "@radix-ui/react-separator";
import { checkSession } from "@/lib/require-session";
import { NavLinks } from "@/components/nav-links";
import { BottomNav } from "@/components/bottom-nav";
import { LanguageSwitcher } from "@/components/language-switcher";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

// The auth+allowlist gate lives HERE, not in middleware.ts, because a real
// build showed Next.js 15.5.23 has no working Node.js middleware runtime —
// `experimental.nodeMiddleware` isn't a recognized key in this version's own
// config validator, and middleware defaults to the Edge runtime, where
// `node:fs` doesn't exist. A layout is an async Server Component and runs in
// Node.js by default, no experimental flag needed. `middleware.ts` still
// exists in this app now (for next-intl locale routing), but it never
// touches fs — that's a different, unrelated concern from this gate.
// `/login` sits outside the `(portal)` route group (both now one level
// deeper under `[locale]`), so it's the one route this never touches.
//
// checkSession() (lib/require-session.ts) is the SAME check every
// app/api/* proxy route calls — this page gate alone never protected them;
// see require-api-session.ts's comment for what was missing without it.
export default async function PortalLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const check = await checkSession();
  if (check.status === "no-session") {
    redirect(`/${locale}/login`);
  }
  if (check.status === "denied") {
    redirect(`/${locale}/login?denied=1`);
  }

  const initials = check.displayName
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="min-h-screen bg-bg-base md:grid md:grid-cols-[240px_1fr]">
      {/* Desktop sidebar — trustless/apps/web's AppShell pattern: sidebar
          owns nav at md+, a fixed bottom tab bar (below) owns it under
          that. Never both at once. `md:h-screen` here is paired with
          `<main>`'s own `md:h-screen` + `md:overflow-y-auto` below — an
          unbounded `<main>` with `overflow-y-auto` does nothing (no height
          to overflow against), so the whole DOCUMENT scrolled as one and
          this sidebar visually scrolled away with the page content instead
          of staying put — the real bug behind "the sidebar looks broken". */}
      <aside className="hidden md:flex md:h-screen md:flex-col md:gap-7 md:border-r md:border-border md:bg-bg-surface md:p-3.5">
        <div className="flex items-center justify-between gap-2 border-b border-border px-2 pb-3">
          <span className="text-[15px] font-semibold uppercase tracking-wide">Nora</span>
          <LanguageSwitcher />
        </div>

        <NavLinks />

        <Separator.Root className="mt-auto h-px bg-border" />

        <div className="flex items-center gap-2 px-1">
          <Avatar>
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
          <span className="truncate text-[13px] text-text-secondary">{check.displayName}</span>
        </div>
      </aside>

      {/* Mobile top bar — brand + language switcher only; NavLinks moves
          into BottomNav below md. */}
      <div className="flex items-center justify-between border-b border-border bg-bg-surface p-3.5 md:hidden">
        <span className="text-[15px] font-semibold tracking-tight">Nora</span>
        <LanguageSwitcher />
      </div>

      {/* The SCROLL CONTAINER spans the full grid cell width (no max-w
          here) so its scrollbar sits at the real right edge of the
          viewport. The width cap for readability lives on the INNER div
          instead — putting max-w on the scrolling element itself made it a
          narrow, left-aligned box inside a much wider column, so its
          scrollbar rendered ~1120px from the left instead of at the
          window edge — on a wide screen, dead center. */}
      <main className="p-4 pb-24 sm:p-6 md:h-screen md:overflow-y-auto md:p-10 md:pb-10">
        <div className="max-w-[880px] mx-auto">{children}</div>
      </main>

      <BottomNav />
    </div>
  );
}
