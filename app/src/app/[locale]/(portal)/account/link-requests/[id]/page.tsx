import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { identityBackend } from "@/server/management-session";
import { readLinkResult } from "@/server/link-consent";
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

// One request, named by id in the path. The sealed result the consent seam
// left behind is read HERE on the server — the view renders it; it can never
// mint one.
export default async function LinkRequestPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) notFound();
  const managed = identityBackend() === "managed";
  const result = managed ? await readLinkResult(id) : undefined;
  return <RequestView requestId={id} managed={managed} result={result} />;
}
