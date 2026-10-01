import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { DocumentPageView } from "./document-view";
import { PresentView } from "./present-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("documents") };
}

export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ view?: string | string[]; slide?: string | string[] }>;
}) {
  const { id } = await params;
  const { view, slide } = await searchParams;
  // `?view=present` is the same document, one Section per slide (design/artifact §32).
  if (view === "present") {
    return <PresentView id={decodeURIComponent(id)} slide={typeof slide === "string" ? slide : undefined} />;
  }
  return <DocumentPageView id={decodeURIComponent(id)} />;
}
