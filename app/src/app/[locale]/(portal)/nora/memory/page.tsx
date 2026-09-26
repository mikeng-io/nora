import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { MemoryPage } from "./memory-page";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("memory") };
}

export default function NoraMemoryPage() {
  return <MemoryPage />;
}
