import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { RevisionView } from "./revision-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "documentViews" });
  return { title: t("historyTitle") };
}

export default async function DocumentRevisionPage({ params }: { params: Promise<{ id: string; n: string }> }) {
  const { id, n } = await params;
  return <RevisionView id={decodeURIComponent(id)} n={n} />;
}
