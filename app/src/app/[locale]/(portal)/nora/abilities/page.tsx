import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AbilitiesView } from "./abilities-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("abilities") };
}

export default function AbilitiesPage() {
  // No Suspense boundary: portal pages are dynamic (the session gate reads
  // cookies), so useSearchParams needs none, and a boundary hydrates late,
  // after the persisted cache may already have been restored, which renders
  // restored data over server HTML that had none.
  return <AbilitiesView />;
}
