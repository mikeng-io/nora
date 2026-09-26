import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { TodayView } from "./today-view";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("today") };
}

export default function TodayPage() {
  return <TodayView />;
}
