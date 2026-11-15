import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { identityBackend } from "@/server/management-session";
import { auth } from "@/auth";
import { managedSessionSchema } from "@/server/management-session";
import { RequestView } from "./request-view";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "accountPage" });
  return { title: t("requestTitle") };
}

// The page carries the session generation, never a prior consent outcome.
export default async function LinkRequestPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) notFound();
  const managed = identityBackend() === "managed";
  const retained = managedSessionSchema.safeParse((await auth())?.user?.managedIdentity);
  const sessionKey = retained.success ? retained.data.authentication.sessionId : "unavailable";
  return <RequestView requestId={id} managed={managed} sessionKey={sessionKey} />;
}
