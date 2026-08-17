import { NextIntlClientProvider } from "next-intl";
import { getMessages, getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import { Inter } from "next/font/google";
import type { Metadata } from "next";
import { routing } from "@/i18n/routing";
import "../globals.css";
import { RegisterServiceWorker } from "../register-sw";

// Self-hosted at build time (next/font/google fetches once during `next
// build`, never at request time) — no runtime request to Google, no CLS
// from a late-loading web font. `variable` feeds `--font-sans` in
// globals.css's @theme block rather than a className on <body>, so
// Tailwind's `font-sans` utility (and the plain `body` rule) resolve to it
// everywhere, not just wherever this className happens to be applied.
const inter = Inter({ subsets: ["latin"], variable: "--font-sans-inter" });

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "metadata" });
  return {
    title: t("title"),
    description: t("description"),
    manifest: "/manifest.json",
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!routing.locales.includes(locale as (typeof routing.locales)[number])) {
    notFound();
  }

  const messages = await getMessages();

  return (
    <html lang={locale} className={inter.variable}>
      <body>
        <NextIntlClientProvider messages={messages} locale={locale}>
          {children}
        </NextIntlClientProvider>
        <RegisterServiceWorker />
      </body>
    </html>
  );
}
