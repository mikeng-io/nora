import { redirect } from "next/navigation";
import * as Separator from "@radix-ui/react-separator";
import { checkSession } from "@/lib/require-session";
import { NavLinks } from "@/components/nav-links";
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
    <div className="grid min-h-screen grid-cols-[240px_1fr] bg-bg-base">
      <aside className="flex flex-col gap-7 border-r border-border bg-bg-surface p-3.5">
        <div className="flex items-center justify-between gap-2 border-b border-border px-2 pb-3">
          <div className="flex items-center gap-2">
            <div className="grid h-[22px] w-[22px] place-items-center border border-text-primary font-mono text-[11px] font-semibold">
              N
            </div>
            <span className="text-[15px] font-semibold tracking-tight">Nora</span>
          </div>
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

      <main className="max-w-[880px] p-10">{children}</main>
    </div>
  );
}
