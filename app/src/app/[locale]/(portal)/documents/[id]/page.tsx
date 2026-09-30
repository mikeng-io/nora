import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { DocumentPageView } from "./document-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("documents") };
}

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DocumentPageView id={decodeURIComponent(id)} />;
}
