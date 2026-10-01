import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { HistoryView } from "./history-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "documentViews" });
  return { title: t("historyTitle") };
}

export default async function DocumentHistoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <HistoryView id={decodeURIComponent(id)} />;
}
