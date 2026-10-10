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
    { id: "google", title: p("googleTitle"), body: p("googleBody", email) },
    { id: "readonly", title: p("readonlyTitle"), body: p("readonlyBody") },
    { id: "model", title: p("modelTitle"), body: p("modelBody") },
    { id: "never", title: p("neverTitle"), body: p("neverBody") },
    {
      id: "limited",
      title: p("limitedTitle"),
      body: p("limitedBody"),
      link: { href: GOOGLE_USER_DATA_POLICY, label: p("limitedLink") },
    },
    {
      id: "revoke",
      title: p("revokeTitle"),
      body: p("revokeBody", email),
      link: { href: GOOGLE_PERMISSIONS, label: p("revokeLink") },
    },
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
