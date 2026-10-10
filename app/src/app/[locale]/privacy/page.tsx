import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { LegalPage, type LegalSection } from "@/components/legal/legal-page";
import { GOOGLE_PERMISSIONS, GOOGLE_USER_DATA_POLICY, LEGAL_CONTACT, LEGAL_EFFECTIVE } from "@/lib/legal";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "legal" });
  // The root layout says noindex for the portal. These two pages are the
  // opposite: a policy a stranger is meant to find.
  return { title: t("metaPrivacy"), robots: { index: true, follow: true } };
}

export default async function PrivacyPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "legal" });
  const p = await getTranslations({ locale, namespace: "legal.privacy" });
  const email = { email: LEGAL_CONTACT };
  const sections: LegalSection[] = [
    { id: "who", title: p("whoTitle"), body: p("whoBody", email) },
    {
      id: "info",
      title: p("infoTitle"),
      body: p("infoBody"),
      items: [p("infoMessages"), p("infoShared"), p("infoConnected"), p("infoLocation"), p("infoLogs")],
    },
    { id: "use", title: p("useTitle"), body: p("useBody") },
    {
      id: "processors",
      title: p("processorsTitle"),
      body: p("processorsBody"),
      items: [
        p("processorsModels"),
        p("processorsPlatforms"),
        p("processorsHosting"),
        p("processorsConnected"),
        p("processorsTools"),
      ],
    },
    { id: "retention", title: p("retentionTitle"), body: p("retentionBody") },
    {
      id: "choices",
      title: p("choicesTitle"),
      body: p("choicesBody", email),
      link: { href: GOOGLE_PERMISSIONS, label: p("choicesLink") },
    },
    { id: "security", title: p("securityTitle"), body: p("securityBody") },
    { id: "never", title: p("neverTitle"), body: p("neverBody") },
    {
      id: "google",
      title: p("googleTitle"),
      body: p("googleBody"),
      link: { href: GOOGLE_USER_DATA_POLICY, label: p("googleLink") },
    },
    { id: "children", title: p("childrenTitle"), body: p("childrenBody") },
    { id: "changes", title: p("changesTitle"), body: p("changesBody") },
  ];
  return (
    <LegalPage
      locale={locale}
      sections={sections}
      labels={{
        title: p("title"),
        effective: t("effective", { date: LEGAL_EFFECTIVE }),
        contact: t("contact", email),
        back: t("back"),
        privacyLink: t("privacyLink"),
        termsLink: t("termsLink"),
      }}
    />
  );
}
