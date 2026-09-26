import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PlayerView } from "./player-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("games") };
}

export default async function PlayerPage({ params }: { params: Promise<{ id: string; playerKey: string }> }) {
  const { id, playerKey } = await params;
  return <PlayerView id={decodeURIComponent(id)} playerKey={decodeURIComponent(playerKey)} />;
}
