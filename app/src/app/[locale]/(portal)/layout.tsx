import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { checkSession } from "@/server/session";
import { QueryProvider } from "@/components/providers/query-provider";
import { AppShell } from "@/components/shell/app-shell";
import { ViewerProvider } from "@/components/shell/viewer";

// The sign-in gate for every portal page. It lives in a layout, which runs
// in Node and can read identity.toml, rather than in proxy.ts. The same
// checkSession() guards every /api/v1 read, so a page gate is never the
// only thing standing in front of the data.
export default async function PortalLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const check = await checkSession();
  if (check.status === "no-session") redirect(`/${locale}/login`);
  if (check.status === "denied") redirect(`/${locale}/login?denied=1`);

  const viewer = {
    personId: check.personId, displayName: check.displayName, image: check.image, via: check.via,
  };
  return (
    <ViewerProvider viewer={viewer}>
      <QueryProvider personId={check.personId} sessionId={check.sessionId}>
        <AppShell>{children}</AppShell>
      </QueryProvider>
    </ViewerProvider>
  );
}
