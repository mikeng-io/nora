import { redirect } from "next/navigation";

// A bare locale root opens the portal's front page; signed out, its layout
// sends the visitor to sign in.
export default async function LocaleRoot({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  redirect(`/${locale}/today`);
}
