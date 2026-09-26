import { redirect } from "next/navigation";

// The portal's locale root is the portal's own front door: its login, which
// sends a signed-in, allowlisted person straight on to /status. The public
// landing page is Nora's site (site/, served at / and /en/ on this hostname
// by the tunnel, site/README.md "Going live"); the copy this route used to
// render went with it. Not a redirect to the site: without the tunnel (local
// dev) `/` reaches this app, whose middleware sends it back here, a loop.
export default async function LocaleRoot({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  redirect(`/${locale}/login`);
}
