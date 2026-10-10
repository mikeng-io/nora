import type { Metadata } from "next";
import { getTranslations, setRequestLocale } from "next-intl/server";
import { LegalPage, type LegalSection } from "@/components/legal/legal-page";
import { LEGAL_CONTACT, LEGAL_EFFECTIVE } from "@/lib/legal";

type Props = { params: Promise<{ locale: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "legal" });
  return { title: t("metaTerms"), robots: { index: true, follow: true } };
}

export default async function TermsPage({ params }: Props) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: "legal" });
  const p = await getTranslations({ locale, namespace: "legal.terms" });
  const email = { email: LEGAL_CONTACT };
  const sections: LegalSection[] = [
    { id: "what", title: p("whatTitle"), body: p("whatBody") },
    { id: "use", title: p("useTitle"), body: p("useBody") },
    { id: "read", title: p("readTitle"), body: p("readBody") },
    { id: "contact", title: p("contactTitle"), body: p("contactBody", email) },
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
