import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PersonView } from "./person-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("people") };
}

export default async function PersonPage({ params }: { params: Promise<{ personId: string }> }) {
  const { personId } = await params;
  return (
    <PersonView personId={decodeURIComponent(personId)} />
  );
}
