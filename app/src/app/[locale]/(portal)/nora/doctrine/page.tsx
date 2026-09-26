import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { DoctrineView } from "./doctrine-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("doctrine") };
}

export default function DoctrinePage() {
  return <DoctrineView />;
}
