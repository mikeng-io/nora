import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { GamesView } from "./games-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("games") };
}

export default function GamesPage() {
  return <GamesView />;
}
