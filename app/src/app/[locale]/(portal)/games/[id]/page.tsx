import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ServerView } from "./server-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("games") };
}

export default async function ServerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <ServerView id={decodeURIComponent(id)} />
  );
}
