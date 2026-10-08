import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ArtifactPageView } from "./artifact-view";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return { title: t("artifacts") };
}

// Where a link Nora sends opens: `<public_url>/artifacts/<artifact_id>` (ADR 0023).
export default async function ArtifactPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ArtifactPageView id={decodeURIComponent(id)} />;
}
