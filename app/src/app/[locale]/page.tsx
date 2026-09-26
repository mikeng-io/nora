import { redirect } from "next/navigation";

// A bare /zh-Hant opens the portal's front page. (On the shared hostname the
// site answers / and /en/ exactly, so only /zh-Hant ever reaches here; in
// local development both do.)
export default async function LocaleRoot({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  redirect(`/${locale}/today`);
}
