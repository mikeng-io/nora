import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { DownloadsView } from "./downloads-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("downloads") };
}

export default function DownloadsPage() {
  return <DownloadsView />;
}
