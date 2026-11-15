import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { auth } from "@/auth";
import { identityBackend, managedSessionSchema } from "@/server/management-session";
import { AccountView } from "./account-view";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "accountPage" });
  return { title: t("title") };
}

// The account surface: the layout gate already admitted a session; the page
// adds only what managed mode can show — in file mode the sign-in card is
// all there is, and no writable managed control renders.
export default async function AccountPage() {
  const managed = identityBackend() === "managed";
  const session = managed ? await auth() : null;
  const retained = managedSessionSchema.safeParse(session?.user?.managedIdentity);
  return (
    <AccountView
      managed={managed}
      account={retained.success ? retained.data.authentication.account : undefined}
      method={retained.success ? retained.data.authentication.method : undefined}
    />
  );
}
